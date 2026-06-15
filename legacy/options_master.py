from __future__ import annotations

import json
import logging
import os
from dataclasses import replace
from typing import Dict, Iterable, List, Optional

from options_broker_router import OptionsBrokerRouter
from options_earnings_adapter import OptionsEarningsAdapter
from options_iv_engine import OptionsIVEngine
from options_logger import OptionsLogger
from options_spread_scanner import OptionsSpreadScanner, SpreadCandidate
from options_state_manager import OptionPositionState, OptionsStateManager
from options_underlying_router import OptionUnderlying, OptionsUnderlyingRouter
from options_universe_scanner import OptionsUniverseScanner

logger = logging.getLogger(__name__)

_STRATEGY_ORDER = ["SNIPER", "SHORT", "VOYAGER", "CONTRARIAN"]


class OptionsMaster:
    """Phase A defined-risk options overlay."""

    def __init__(
        self,
        trading_client=None,
        db_path: str = "trading_performance.db",
        account_size: float = 100000.0,
        execution_mode: str = "PAPER",
    ):
        self.db_path = db_path
        self.account_size = float(account_size or 0.0)
        self.execution_mode = str(execution_mode or "PAPER").upper()
        self.options_logger = OptionsLogger(db_path=db_path)
        self.router = OptionsUnderlyingRouter(
            db_path=db_path,
            allow_equity_overlap=str(os.getenv("OPTIONS_ALLOW_EQUITY_OVERLAP", "0")).strip().lower() in {"1", "true", "yes", "on"},
        )
        self.earnings = OptionsEarningsAdapter()
        self.iv_engine = OptionsIVEngine(db_path=db_path, logger_store=self.options_logger)
        self.spread_scanner = OptionsSpreadScanner()
        self.state_manager = OptionsStateManager(db_path=db_path, logger_store=self.options_logger)
        self.broker = OptionsBrokerRouter(trading_client=trading_client, execution_mode=self.execution_mode)
        # Standalone options universe scanner — runs independently of equity signals
        self.universe_scanner = OptionsUniverseScanner(
            iv_engine=self.iv_engine,
            earnings_adapter=self.earnings,
        )
        self.max_position_risk_pct = float(os.getenv("OPTIONS_MAX_POSITION_RISK_PCT", "0.75") or 0.75)
        self.max_portfolio_risk_pct = float(os.getenv("OPTIONS_MAX_PORTFOLIO_RISK_PCT", "4.0") or 4.0)
        self.max_positions = int(os.getenv("OPTIONS_MAX_OPEN_POSITIONS", "3") or 3)
        self.earnings_blackout_days = int(os.getenv("OPTIONS_EARNINGS_BLACKOUT_DAYS", "7") or 7)
        self.strict_earnings_live = str(os.getenv("OPTIONS_REQUIRE_EARNINGS_DATA", "0")).strip().lower() in {"1", "true", "yes", "on"}

    def is_enabled(self) -> bool:
        return self.broker.is_enabled()

    @staticmethod
    def _extract_vix(regime_snapshot: Optional[Dict]) -> float:
        """Pull current VIX level from the regime snapshot produced by the master trader."""
        if not regime_snapshot:
            return 0.0
        return float(regime_snapshot.get("vix_level") or 0.0)

    def run_options_cycle(
        self,
        *,
        regime_snapshot: Optional[Dict],
        approved_opportunities: Iterable,
        reject_rows: Iterable[Dict],
        equity_positions: Optional[Dict],
        run_id: Optional[str],
    ) -> Dict:
        summary = {
            "managed": 0,
            "closed": 0,
            "opened": 0,
            "skipped": 0,
            "eligible_underlyings": 0,
            "errors": 0,
        }
        progress = getattr(self, "_progress_callback", None)
        if not self.is_enabled():
            return summary

        try:
            if callable(progress):
                try:
                    progress("manage_open")
                except Exception:
                    pass
            self._manage_open_positions(summary)
            grouped = self.router.build(
                approved_opportunities=approved_opportunities,
                reject_rows=reject_rows,
                equity_positions=equity_positions,
            )
            summary["eligible_underlyings"] = sum(len(v) for v in grouped.values())
            open_positions = self.state_manager.load_open_positions()
            open_tickers = {pos.ticker for pos in open_positions}
            current_portfolio_risk = sum(float(pos.max_risk_usd or 0.0) for pos in open_positions)
            # VIX passed through to scanner so it can open the Voyager gate at VIX > 25
            vix_level = self._extract_vix(regime_snapshot)

            # Merge candidates from the standalone options universe scanner.
            # These are options-first picks (deep liquid chains, IV-ranked)
            # that exist independent of whether any equity signal fired.
            # They are added to the VOYAGER bucket so the spread scanner routes
            # them to BULL_PUT_CREDIT when IV is FAIR/RICH.
            # Dedup: equity signals take priority; universe scanner fills gaps.
            equity_tickers_in_grouped = {u.ticker for bucket in grouped.values() for u in bucket}
            try:
                universe_candidates = self.universe_scanner.scan(vix_level=vix_level)
                new_from_universe = [
                    u for u in universe_candidates
                    if u.ticker not in equity_tickers_in_grouped
                ]
                if new_from_universe:
                    grouped.setdefault("VOYAGER", []).extend(new_from_universe)
                    summary["eligible_underlyings"] += len(new_from_universe)
                    logger.info(
                        "[OPTIONS] universe scanner added %d candidates (total pool now %d)",
                        len(new_from_universe), summary["eligible_underlyings"],
                    )
            except Exception as _uni_exc:
                logger.debug("[OPTIONS] universe scanner error (non-fatal): %s", _uni_exc)

            for strategy in _STRATEGY_ORDER:
                underlyings = grouped.get(strategy, [])
                total = len(underlyings)
                for idx, underlying in enumerate(underlyings, start=1):
                    if callable(progress):
                        try:
                            progress(f"{strategy}:{idx}/{total}:{underlying.ticker}")
                        except Exception:
                            pass
                    if underlying.ticker in open_tickers:
                        continue
                    if len(open_tickers) >= self.max_positions:
                        return summary
                    iv_context = self.iv_engine.get_iv_context(underlying.ticker)
                    if not iv_context:
                        self._log_skip(run_id, underlying, "no_iv_context")
                        summary["skipped"] += 1
                        continue
                    # Inject current VIX so the scanner can apply the VIX-level gate
                    iv_context["vix_level"] = vix_level
                    if self.earnings.should_block_new_trade(
                        underlying.ticker,
                        blackout_days=self.earnings_blackout_days,
                        strict_when_unknown=(self.strict_earnings_live and not self.broker.paper_mode),
                    ):
                        self._log_skip(run_id, underlying, "earnings_blackout")
                        summary["skipped"] += 1
                        continue
                    candidate, reason = self.spread_scanner.scan_underlying(underlying, iv_context)
                    scan_diag = dict(getattr(self.spread_scanner, "last_diagnostics", {}) or {})
                    if candidate is None:
                        self._log_skip(
                            run_id,
                            underlying,
                            reason or "scanner_no_candidate",
                            iv_context=iv_context,
                            notes=scan_diag,
                        )
                        summary["skipped"] += 1
                        continue
                    candidate = self._size_candidate(candidate, current_portfolio_risk)
                    if candidate is None:
                        risk_notes = {
                            "candidate_max_risk_usd": float(getattr(candidate, "max_risk_usd", 0.0) or 0.0),
                            "position_risk_budget_usd": round(self.account_size * (self.max_position_risk_pct / 100.0), 2),
                            "portfolio_risk_budget_usd": round(self.account_size * (self.max_portfolio_risk_pct / 100.0), 2),
                            "current_portfolio_risk_usd": round(float(current_portfolio_risk or 0.0), 2),
                            "remaining_portfolio_risk_usd": round(
                                (self.account_size * (self.max_portfolio_risk_pct / 100.0)) - float(current_portfolio_risk or 0.0),
                                2,
                            ),
                        }
                        self._log_skip(
                            run_id,
                            underlying,
                            "risk_budget_exceeded",
                            iv_context=iv_context,
                            notes=risk_notes,
                        )
                        summary["skipped"] += 1
                        continue
                    candidate_id = self._log_candidate(run_id, candidate)
                    order = self.broker.submit_open(candidate)
                    if not order.submitted:
                        self.options_logger.update_candidate(candidate_id, status="ORDER_REJECTED", reason=order.message)
                        summary["skipped"] += 1
                        continue
                    position_id = self.state_manager.open_from_candidate(
                        candidate,
                        run_id=run_id,
                        broker=order.broker,
                        paper_mode=order.paper_mode,
                        candidate_id=candidate_id,
                    )
                    self.options_logger.update_candidate(candidate_id, status="OPENED", broker_order_id=order.order_id)
                    open_tickers.add(candidate.ticker)
                    current_portfolio_risk += float(candidate.max_risk_usd or 0.0)
                    summary["opened"] += 1
                    logger.info(
                        "[OPTIONS] OPEN %s %s %s %s @ %.2f (risk $%.2f)",
                        candidate.ticker,
                        candidate.underlying_strategy,
                        candidate.structure_type,
                        order.broker,
                        candidate.entry_net_price,
                        candidate.max_risk_usd,
                    )
            return summary
        except Exception as exc:
            logger.warning("[OPTIONS] cycle error (non-fatal): %s", exc)
            summary["errors"] += 1
            return summary

    def _manage_open_positions(self, summary: Dict) -> None:
        for position in self.state_manager.load_open_positions():
            mark = self.spread_scanner.mark_position(
                position.ticker,
                position.entry_expiry,
                position.entry_is_credit,
                [
                    {
                        "leg_role": leg.leg_role,
                        "contract_symbol": leg.contract_symbol,
                        "option_type": leg.option_type,
                    }
                    for leg in position.legs
                ],
            )
            if not mark:
                continue
            pnl = self.state_manager.pnl_snapshot(position, mark["current_mark"])
            self.state_manager.update_mark(
                position.id,
                current_mark=mark["current_mark"],
                total_pnl_usd=pnl["total_pnl_usd"],
                total_pnl_pct=pnl["total_pnl_pct"],
                notes={
                    "current_dte": mark["current_dte"],
                    "short_delta": mark.get("short_delta"),
                },
            )
            summary["managed"] += 1
            actions = self.state_manager.evaluate_actions(
                position,
                current_mark=mark["current_mark"],
                current_dte=int(mark.get("current_dte") or 0),
                short_delta=mark.get("short_delta"),
                earnings_blackout=self.earnings.is_blackout(position.ticker, blackout_days=self.earnings_blackout_days),
                thesis_broken=self._is_thesis_broken(position, float(mark.get("underlying_price") or 0.0)),
            )
            if not actions:
                continue
            reason = actions[0]
            order = self.broker.submit_close(position, mark["current_mark"])
            if not order.submitted:
                continue
            self.state_manager.close_position(
                position.id,
                close_reason=reason,
                current_mark=mark["current_mark"],
                total_pnl_usd=pnl["total_pnl_usd"],
                total_pnl_pct=pnl["total_pnl_pct"],
                underlying_exit_price=float(mark.get("underlying_price") or 0.0),
                leg_exit_prices=mark.get("leg_marks"),
                notes={
                    "close_order_id": order.order_id,
                    "close_broker": order.broker,
                    "close_action": reason,
                },
            )
            summary["closed"] += 1
            logger.info(
                "[OPTIONS] CLOSE %s %s reason=%s pnl=$%.2f",
                position.ticker,
                position.structure_type,
                reason,
                pnl["total_pnl_usd"],
            )

    def _is_thesis_broken(self, position: OptionPositionState, underlying_price: float) -> bool:
        if underlying_price <= 0:
            return False
        try:
            notes = json.loads(position.notes) if position.notes else {}
        except Exception:
            notes = {}
        stop = float(notes.get("underlying_stop_loss") or 0.0)
        if stop <= 0:
            return False
        if str(position.underlying_direction).upper() == "SHORT":
            return underlying_price >= stop
        return underlying_price <= stop

    def _size_candidate(self, candidate: SpreadCandidate, current_portfolio_risk: float) -> Optional[SpreadCandidate]:
        position_risk_budget = self.account_size * (self.max_position_risk_pct / 100.0)
        portfolio_risk_budget = self.account_size * (self.max_portfolio_risk_pct / 100.0)
        remaining_portfolio_risk = portfolio_risk_budget - float(current_portfolio_risk or 0.0)
        per_contract_risk = float(candidate.max_risk_usd or 0.0)
        if per_contract_risk <= 0 or remaining_portfolio_risk <= 0:
            return None
        contracts = int(min(position_risk_budget, remaining_portfolio_risk) // per_contract_risk)
        if contracts < 1:
            return None
        return replace(
            candidate,
            contracts=contracts,
            max_risk_usd=round(float(candidate.max_risk_usd) * contracts, 2),
            max_profit_usd=round(float(candidate.max_profit_usd) * contracts, 2),
        )

    def _log_skip(
        self,
        run_id: Optional[str],
        underlying: OptionUnderlying,
        reason: str,
        iv_context: Optional[Dict] = None,
        notes: Optional[Dict] = None,
    ) -> None:
        self.options_logger.log_candidate(
            {
                "run_id": run_id,
                "ticker": underlying.ticker,
                "underlying_strategy": underlying.strategy,
                "underlying_direction": underlying.direction,
                "source_type": underlying.source_type,
                "status": "SKIPPED",
                "reason": reason,
                "entry_price": underlying.entry_price,
                "stop_loss": underlying.stop_loss,
                "target_price": underlying.target_price,
                "equity_rr": underlying.equity_rr,
                "underlying_score": underlying.score,
                "options_pcr": underlying.options_pcr,
                "options_gamma": underlying.options_gamma,
                "iv_rank_30d": (iv_context or {}).get("iv_rank_30d"),
                "iv_rank_252d": (iv_context or {}).get("iv_rank_252d"),
                "iv_regime": (iv_context or {}).get("iv_regime"),
                "notes": notes,
            }
        )

    def _log_candidate(self, run_id: Optional[str], candidate: SpreadCandidate) -> int:
        return self.options_logger.log_candidate(
            {
                "run_id": run_id,
                "ticker": candidate.ticker,
                "underlying_strategy": candidate.underlying_strategy,
                "underlying_direction": candidate.underlying_direction,
                "source_type": candidate.source_type,
                "structure_type": candidate.structure_type,
                "status": "CANDIDATE",
                "entry_price": candidate.underlying_entry_price,
                "stop_loss": candidate.underlying_stop_loss,
                "target_price": candidate.underlying_target_price,
                "equity_rr": candidate.equity_rr,
                "underlying_score": candidate.underlying_score,
                "options_pcr": candidate.options_pcr,
                "options_gamma": candidate.options_gamma,
                "iv_rank_30d": candidate.iv_rank_30d,
                "iv_rank_252d": candidate.iv_rank_252d,
                "iv_regime": candidate.iv_regime,
                "expiry": candidate.expiry,
                "dte": candidate.dte,
                "short_contract": candidate.short_leg.contract_symbol,
                "long_contract": candidate.long_leg.contract_symbol,
                "net_price": candidate.entry_net_price,
                "width": candidate.width,
                "max_risk": candidate.max_risk_usd,
                "score": candidate.score,
                "notes": candidate.notes,
            }
        )
