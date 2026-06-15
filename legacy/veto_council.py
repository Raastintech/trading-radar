# Change FROM this:
from vanna_charm import VannaCharmAnalyzer
from flow_divergence import FlowDivergenceDetector
from portfolio_manager import PortfolioManager
from market_regime import MarketRegimeAnalyzer
from signal_generator import MasterSignalGenerator

# TO this:
import sys
import os
import uuid
from datetime import datetime

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Now import
try:
    from vanna_charm import VannaCharmAnalyzer
    from flow_divergence import FlowDivergenceDetector
    from sentiment_analyzer_v2 import RealSentimentAnalyzer as SentimentAnalyzer
    from portfolio_manager import PortfolioManager
    from market_regime import MarketRegimeAnalyzer
    from macro_veto_agent import MacroVetoAgent
    from sector_rotation_agent import SectorRotationAgent
    from execution_guard import ExecutionGuard
    from regime_filter import RegimeFilter
    from signal_generator import MasterSignalGenerator
    from decision_logger import DecisionLogger
    from system_event_logger import SystemEventLogger
    from decision_contract import normalize_council_decision, normalize_vote
except ImportError as e:
    print(f"Import error: {e}")
    print("\nMake sure all these files exist in the same directory:")
    print("  - vanna_charm.py")
    print("  - flow_divergence.py")
    print("  - sentiment_analyzer.py")
    print("  - portfolio_manager.py")
    print("  - market_regime.py")
    print("  - signal_generator.py")
    sys.exit(1)

class VetoCouncil:
    """
    Multi-agent decision making system
    All agents must vote on each trade
    ANY agent can VETO
    """
    
    def __init__(self, use_flow_agent=False, use_sentiment_agent=True, strict_run_id=True):
        """
        Initialize Veto Council
        
        Args:
            use_flow_agent: Enable Flow agent (disabled - needs real data)
            use_sentiment_agent: Enable Sentiment agent (proxy indicators)
        """
        # Initialize all agents
        self.greek_agent = VannaCharmAnalyzer()
        
        # Flow agent - disabled by default
        self.use_flow_agent = use_flow_agent
        if use_flow_agent:
            self.flow_agent = FlowDivergenceDetector()
            print("⚠️  Flow agent ENABLED (using proxy indicators)")
        else:
            self.flow_agent = None
            print("ℹ️  Flow agent DISABLED")

        # Sentiment agent - NEW!
        self.use_sentiment_agent = use_sentiment_agent
        if use_sentiment_agent:
            self.sentiment_agent = SentimentAnalyzer()
            print("✅ Sentiment agent ENABLED (using price/volume proxy)")
        else:
            self.sentiment_agent = None
            print("ℹ️  Sentiment agent DISABLED")
        
        self.portfolio_agent = PortfolioManager()
        self.regime_agent = MarketRegimeAnalyzer()
        self.macro_agent = MacroVetoAgent()
        self.sector_agent = SectorRotationAgent()
        self.execution_guard = ExecutionGuard(
            account_size=None,
            risk_pct=0.05
        )
        print("✅ Execution Guard enabled")
        self.regime_filter = RegimeFilter()
        print("✅ Regime Filter enabled")
        self.signal_gen = MasterSignalGenerator()
        self.logger = DecisionLogger()
        self.system_logger = SystemEventLogger()
        self.run_id = None  # set by scanner (master_alignment/auto_trader)
        self._decision_cache = {}  # ticker -> decision dict
        self.mandatory_agents = {"portfolio", "execution_guard", "regime_filter"}
        self.strict_run_id = strict_run_id
        self.auto_create_manual_runs = True
        
        agent_count = 7 + (1 if use_sentiment_agent else 0) + (1 if use_flow_agent else 0)
        print(f"✅ Veto Council initialized - {agent_count} agents active")

    def set_run(self, run_id: str):
        self.run_id = run_id
        self._decision_cache = {}

    def _calculate_shares(self, signal):
        """
        Calculate shares based on a simple 1% risk model.
        Uses a fixed paper account reference for deterministic safety checks.
        """
        if not isinstance(signal, dict):
            return 100

        # Use actual account size from ExecutionGuard (which fetches from Alpaca)
        account_size = getattr(self.execution_guard, 'account_size', 50000)
        risk_pct = getattr(self.execution_guard, 'risk_pct', 0.01)
        risk_dollars = account_size * risk_pct

        entry = signal.get("entry_price", 0)
        stop = signal.get("stop_loss", 0)

        try:
            entry = float(entry or 0)
            stop = float(stop or 0)
        except Exception:
            return 100

        if entry <= 0 or stop <= 0:
            return 100

        risk_per_share = entry - stop
        if risk_per_share <= 0:
            return 100

        shares = int(risk_dollars / risk_per_share)
        return max(1, min(shares, 1000))

    def _ensure_run_context(self, ticker: str, show_details: bool, run_id: str = None):
        if run_id:
            self.set_run(run_id)
            return
        if self.run_id:
            return
        # Always provision a run context so scan-time evaluate_trade() calls
        # cannot fail when upstream scanner forgot to seed run_id.
        auto_run_id = f"auto_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        self.set_run(auto_run_id)
        self.system_logger.log(
            component="veto_council",
            severity="WARN" if self.strict_run_id else "INFO",
            run_id=self.run_id,
            error_type="AUTO_RUN_ID_CREATED",
            message="run_id missing; auto-generated for evaluate_trade",
            details={"ticker": ticker, "show_details": show_details},
        )
        if show_details:
            print(f"ℹ️  Auto-created run_id: {auto_run_id}")
        return
    
    def evaluate_trade(self, ticker, show_details=True, signal=None, market_session=None, run_id=None):
        """
        Get votes from all agents
        Returns: EXECUTE, CAUTION, or REJECT
        """
        ticker = ticker.upper()
        self._ensure_run_context(ticker=ticker, show_details=show_details, run_id=run_id)

        if not show_details and ticker in self._decision_cache:
            return self._decision_cache[ticker]
        
        if show_details:
            print(f"\n{'='*80}")
            print(f"🗳️  VETO COUNCIL VOTING: {ticker}")
            print(f"{'='*80}\n")
        
        # Collect votes from each agent
        votes = {}
        
        # Agent 1: Vanna & Charm (Greeks)
        try:
            greek_vote = self.greek_agent.vote_on_trade(ticker)
            votes['greeks'] = greek_vote
            if show_details:
                self._display_vote("Vanna/Charm Agent", greek_vote)
        except Exception as e:
            votes['greeks'] = {
                'vote': 'ABSTAIN',
                'reason': f'Agent error: {type(e).__name__}: {e}',
                'score': None,
                'agent_error': True,
                'agent_error_type': type(e).__name__,
                'agent_error_msg': str(e),
            }
            self.system_logger.log(
                component="veto_council.greeks",
                severity="WARN",
                run_id=self.run_id,
                error_type=type(e).__name__,
                message=str(e),
                details={"ticker": ticker},
            )
            if show_details:
                print(f"⚠️  Greek Agent error: {e}\n")
        
        # Agent 2: Flow Divergence (OPTIONAL)
        if self.use_flow_agent and self.flow_agent:
            try:
                flow_vote = self.flow_agent.vote_on_trade(ticker)
                votes['flow'] = flow_vote
                if show_details:
                    self._display_vote("Flow Divergence Agent", flow_vote)
            except Exception as e:
                votes['flow'] = {
                    'vote': 'ABSTAIN',
                    'reason': f'Agent error: {type(e).__name__}: {e}',
                    'score': None,
                    'agent_error': True,
                    'agent_error_type': type(e).__name__,
                    'agent_error_msg': str(e),
                }
                self.system_logger.log(
                    component="veto_council.flow",
                    severity="WARN",
                    run_id=self.run_id,
                    error_type=type(e).__name__,
                    message=str(e),
                    details={"ticker": ticker},
                )
                if show_details:
                    print(f"⚠️  Flow Agent error: {e}\n")
        else:
            # Flow agent disabled - not voting
            if show_details:
                print("ℹ️  Flow Divergence Agent: DISABLED (no vote)\n")
        
        # Agent 3: Portfolio Risk
        try:
            portfolio_vote = self.portfolio_agent.vote_on_trade(
                ticker,
                direction=(signal.get("direction") if isinstance(signal, dict) else None),
            )
            votes['portfolio'] = portfolio_vote
            if show_details:
                self._display_vote("Portfolio Risk Agent", portfolio_vote)
        except Exception as e:
            votes['portfolio'] = {
                'vote': 'ABSTAIN',
                'reason': f'Agent error: {type(e).__name__}: {e}',
                'score': None,
                'agent_error': True,
                'agent_error_type': type(e).__name__,
                'agent_error_msg': str(e),
            }
            self.system_logger.log(
                component="veto_council.portfolio",
                severity="WARN",
                run_id=self.run_id,
                error_type=type(e).__name__,
                message=str(e),
                details={"ticker": ticker},
            )
            if show_details:
                print(f"⚠️  Portfolio Agent error: {e}\n")
        
        # Agent 4: Sentiment (NEW!)
        if self.use_sentiment_agent and self.sentiment_agent:
            try:
                sentiment_vote = self.sentiment_agent.vote_on_trade(ticker)
                votes['sentiment'] = sentiment_vote
                if show_details:
                    self._display_vote("Sentiment Agent", sentiment_vote)
            except Exception as e:
                votes['sentiment'] = {
                    'vote': 'ABSTAIN',
                    'reason': f'Agent error: {type(e).__name__}: {e}',
                    'score': None,
                    'agent_error': True,
                    'agent_error_type': type(e).__name__,
                    'agent_error_msg': str(e),
                }
                self.system_logger.log(
                    component="veto_council.sentiment",
                    severity="WARN",
                    run_id=self.run_id,
                    error_type=type(e).__name__,
                    message=str(e),
                    details={"ticker": ticker},
                )
                if show_details:
                    print(f"⚠️  Sentiment Agent error: {e}\n")
        else:
            if show_details:
                print("ℹ️  Sentiment Agent: DISABLED (no vote)\n")

        # Agent 5: Market Regime (was Agent 4)
        try:
            regime_vote = self.regime_agent.vote_on_trade(ticker)
            votes['regime'] = regime_vote
            if show_details:
                self._display_vote("Market Regime Agent", regime_vote)
        except Exception as e:
            votes['regime'] = {
                'vote': 'ABSTAIN',
                'reason': f'Agent error: {type(e).__name__}: {e}',
                'score': None,
                'agent_error': True,
                'agent_error_type': type(e).__name__,
                'agent_error_msg': str(e),
            }
            self.system_logger.log(
                component="veto_council.regime",
                severity="WARN",
                run_id=self.run_id,
                error_type=type(e).__name__,
                message=str(e),
                details={"ticker": ticker},
            )
            if show_details:
                print(f"⚠️  Regime Agent error: {e}\n")

        # Agent X: Macro / Event Risk
        try:
            macro_vote = self.macro_agent.vote_on_trade(ticker)
            votes["macro"] = macro_vote
            if show_details:
                self._display_vote("Macro Risk Agent", macro_vote)
        except Exception as e:
            votes["macro"] = {
                "vote": "ABSTAIN",
                "reason": f"Agent error: {type(e).__name__}: {e}",
                "score": None,
                "agent_error": True,
                "agent_error_type": type(e).__name__,
                "agent_error_msg": str(e),
            }
            self.system_logger.log(
                component="veto_council.macro",
                severity="WARN",
                run_id=self.run_id,
                error_type=type(e).__name__,
                message=str(e),
                details={"ticker": ticker},
            )

        # Agent Y: Sector Rotation / Relative Strength
        try:
            sector_vote = self.sector_agent.vote(ticker)
            votes["sector_rotation"] = sector_vote
            if show_details:
                self._display_vote("Sector Rotation Agent", sector_vote)
        except Exception as e:
            votes["sector_rotation"] = {
                "vote": "ABSTAIN",
                "reason": f"Agent error: {type(e).__name__}: {e}",
                "score": None,
                "agent_error": True,
                "agent_error_type": type(e).__name__,
                "agent_error_msg": str(e),
            }
            self.system_logger.log(
                component="veto_council.sector_rotation",
                severity="WARN",
                run_id=self.run_id,
                error_type=type(e).__name__,
                message=str(e),
                details={"ticker": ticker},
            )

        # Agent 7: Execution Guard
        try:
            shares = self._calculate_shares(signal)
            execution_vote = self.execution_guard.vote_on_trade(
                ticker=ticker,
                entry_price=(signal.get("entry_price") if isinstance(signal, dict) else None),
                stop_loss=(signal.get("stop_loss") if isinstance(signal, dict) else None),
                shares=shares,
            )
            votes["execution_guard"] = execution_vote
            if show_details:
                self._display_vote("Execution Guard Agent", execution_vote)
        except Exception as e:
            votes["execution_guard"] = {
                "vote": "ABSTAIN",
                "reason": f"Agent error: {type(e).__name__}: {e}",
                "score": None,
                "agent_error": True,
                "agent_error_type": type(e).__name__,
                "agent_error_msg": str(e),
            }
            self.system_logger.log(
                component="veto_council.execution_guard",
                severity="WARN",
                run_id=self.run_id,
                error_type=type(e).__name__,
                message=str(e),
                details={"ticker": ticker},
            )

        # Agent 8: Regime Filter
        try:
            regime_filter_vote = self.regime_filter.vote_on_trade(ticker)
            votes["regime_filter"] = regime_filter_vote
            if show_details:
                self._display_vote("Regime Filter Agent", regime_filter_vote)
        except Exception as e:
            votes["regime_filter"] = {
                "vote": "ABSTAIN",
                "reason": f"Regime check error: {e}",
                "score": None,
                "agent_error": True,
                "agent_error_type": "REGIME_ERROR",
                "agent_error_msg": str(e),
            }
            self.system_logger.log(
                component="veto_council.regime_filter",
                severity="WARN",
                run_id=self.run_id,
                error_type=type(e).__name__,
                message=str(e),
                details={"ticker": ticker},
            )
        
        # Tally votes
        try:
            decision = self._tally_votes(votes)
            decision["raw_votes"] = votes  # always attach for DB
        except Exception as e:
            import traceback
            print("❌ TALLY ERROR:", ticker)
            traceback.print_exc()
            decision = self._build_data_error_decision(e, subsystem="tally")
            decision["raw_votes"] = votes  # preserve partial votes for diagnostics
            self.system_logger.log(
                component="veto_council.tally",
                severity="ERROR",
                run_id=self.run_id,
                error_type=type(e).__name__,
                message=str(e),
                details={"ticker": ticker},
            )

        decision = normalize_council_decision(decision, raw_votes=votes)

        # run_id is enforced via _ensure_run_context
        try:
            self.logger.log_decision(
                run_id=self.run_id,
                ticker=ticker,
                council_decision=decision,
                signal=signal,
                market_session=market_session,
            )
            print(f"✅ Decision logged: {ticker} | {decision.get('decision')} | run_id={self.run_id}")
        except Exception as e:
            print(f"❌ Decision logging failed for {ticker}: {e}")
            self.system_logger.log(
                component="veto_council.decision_logger",
                severity="ERROR",
                run_id=self.run_id,
                error_type=type(e).__name__,
                message=str(e),
                details={"ticker": ticker},
            )
        
        if show_details:
            self._display_decision(decision)

        # Cache the final decision for this run
        self._decision_cache[ticker] = decision
        return decision

    def _build_data_error_decision(self, exc: Exception, subsystem: str = "unknown"):
        err = f"{type(exc).__name__}: {exc}"
        return {
            "decision": "DATA_ERROR",
            "reason": err,
            "approve_count": 0,
            "caution_count": 0,
            "veto_count": 0,
            "avg_score": 0,
            "veto_reasons": [f"DATA_ERROR[{subsystem}]: {err}"],
            "raw_votes": {
                "data": {
                    "vote": "ABSTAIN",
                    "reason": f"DATA_ERROR[{subsystem}]: {err}",
                    "score": None,
                }
            },
        }

    def _build_logic_error_decision(self, exc: Exception, subsystem: str = "unknown"):
        err = f"{type(exc).__name__}: {exc}"
        return {
            "decision": "LOGIC_ERROR",
            "reason": err,
            "approve_count": 0,
            "caution_count": 0,
            "veto_count": 0,
            "avg_score": 0,
            "veto_reasons": [f"LOGIC_ERROR[{subsystem}]: {err}"],
            "raw_votes": {
                "logic": {
                    "vote": "ABSTAIN",
                    "reason": f"LOGIC_ERROR[{subsystem}]: {err}",
                    "score": None,
                }
            },
        }

    def _normalize_vote(self, v):
        return normalize_vote(v)
    
    def _display_vote(self, agent_name, vote):
        """Display individual agent vote"""
        
        vote_result = vote['vote']
        
        if vote_result == 'APPROVE':
            emoji = "✅"
        elif vote_result == 'VETO':
            emoji = "❌"
        elif vote_result == 'ABSTAIN':
            emoji = "⚪"
        else:
            emoji = "⚠️"
        
        print(f"{emoji} {agent_name}: {vote_result}")
        print(f"   Reason: {vote['reason']}")
        
        # Only show score if not None
        if vote.get('score') is not None:
            print(f"   Score: {vote['score']}/100\n")
        else:
            print(f"   Score: N/A\n")
    
    def _tally_votes(self, votes):
        """
        Tally votes - handles 3-agent or 4-agent mode
        """
        votes = {k: self._normalize_vote(v) for k, v in (votes or {}).items()}

        def _score(v):
            s = v.get("score", None)
            if s is None:
                return None
            try:
                return float(s)
            except Exception:
                return None

        # Mandatory-agent gate is council-owned and deterministic.
        mandatory_errors = []
        for agent in sorted(self.mandatory_agents):
            av = votes.get(agent, {})
            if av.get("agent_error"):
                mandatory_errors.append((agent, av))
        if mandatory_errors:
            err_summary = ", ".join(
                f"{agent}:{av.get('agent_error_type')}" for agent, av in mandatory_errors
            )
            err_detail = [
                f"{agent} error: {av.get('agent_error_msg')}" for agent, av in mandatory_errors
            ]
            self.system_logger.log(
                component="veto_council.mandatory_gate",
                severity="ERROR",
                run_id=self.run_id,
                error_type="MANDATORY_AGENT_ERROR",
                message=err_summary,
                details={"errors": err_detail},
            )
            return {
                'decision': 'SYSTEM_REJECT',
                'confidence': 'N/A',
                'approve_count': 0,
                'veto_count': len(mandatory_errors),
                'caution_count': 0,
                'avg_score': 0,
                'total_agents': len(votes),
                'reason': f"Mandatory agent error(s): {err_summary}",
                'veto_reasons': err_detail,
                'position_size_multiplier': 0,
                'errors': err_detail,
            }

        # Remove ABSTAIN votes
        active_votes = {
            k: v for k, v in votes.items()
            if v.get('vote') not in ['ABSTAIN', None]
        }

        # Count votes
        approve_count = sum(1 for v in active_votes.values() if v.get('vote') == 'APPROVE')
        veto_count = sum(1 for v in active_votes.values() if v.get('vote') == 'VETO')
        caution_count = sum(1 for v in active_votes.values() if v.get('vote') == 'CAUTION')

        total_agents = len(active_votes)

        # Calculate average score (for reporting)
        valid_scores = [s for s in (_score(v) for v in active_votes.values()) if s is not None]
        avg_score = sum(valid_scores) / len(valid_scores) if valid_scores else 50

        # --- NEW: Conviction score (ignore neutral anchors 45-55) ---
        conviction_scores = [s for s in valid_scores if s is not None and (s < 45 or s > 55)]
        decision_score = (sum(conviction_scores) / len(conviction_scores)) if conviction_scores else avg_score

        # Get individual votes
        greek_approved = votes.get('greeks', {}).get('vote') == 'APPROVE'
        sentiment_approved = votes.get('sentiment', {}).get('vote') == 'APPROVE'
        portfolio_approved = votes.get('portfolio', {}).get('vote') == 'APPROVE'
        regime_approved = votes.get('regime', {}).get('vote') == 'APPROVE'

        greek_score = _score(votes.get('greeks', {}))
        sentiment_score = _score(votes.get('sentiment', {}))
        greek_score_str = "N/A" if greek_score is None else f"{greek_score:.0f}"
        sent_score_str = "N/A" if sentiment_score is None else f"{sentiment_score:.1f}"
        greek_strong = greek_score is not None and greek_score >= 75

        sentiment_vote = votes.get('sentiment', {}).get('vote')
        portfolio_vote = votes.get('portfolio', {}).get('vote')

        # =========================
        # POLICY: Greeks Optionality + Regime Sync
        # =========================
        greeks_vote = votes.get('greeks', {}).get('vote')
        greeks_score = greek_score
        greeks_available = greeks_vote in ('APPROVE', 'CAUTION')
        greeks_missing = greeks_vote in ('ABSTAIN', None)
        greeks_error = bool(votes.get('greeks', {}).get('agent_error', False))

        regime_vote = votes.get('regime', {}).get('vote')
        regime_details = votes.get('regime', {}).get('details') or {}
        regime_vol = (regime_details.get('volatility') or "").upper()
        regime_status = (regime_details.get('status') or "").upper()

        # Fallback only when structured details are absent.
        if not regime_vol:
            regime_reason = (votes.get('regime', {}).get('reason') or "").upper()
            if "HIGH VOL" in regime_reason or "HIGH_VOL" in regime_reason:
                regime_vol = "HIGH"

        is_high_vol = (regime_vol == "HIGH") or (regime_status == "RISK_OFF")

        # In high-vol, missing/errored Greeks is a hard safety reject.
        if is_high_vol and (greeks_missing or greeks_error):
            msg = f"Greeks unavailable during HIGH_VOL (vote={greeks_vote}, error={greeks_error})"
            self.system_logger.log(
                component="veto_council.policy_gate",
                severity="ERROR",
                run_id=self.run_id,
                error_type="POLICY_BLOCK",
                message=msg,
                details={
                    "policy": "HIGH_VOL_REQUIRES_GREEKS",
                    "greeks_vote": greeks_vote,
                    "greeks_error": greeks_error,
                    "regime_vol": regime_vol,
                    "regime_status": regime_status,
                },
            )
            return {
                'decision': 'SYSTEM_REJECT',
                'confidence': 'N/A',
                'approve_count': approve_count,
                'veto_count': veto_count + 1,
                'caution_count': caution_count,
                'avg_score': round(avg_score, 1),
                'total_agents': len(votes),
                'reason': 'Policy block: HIGH_VOL requires Greeks signal',
                'veto_reasons': [msg],
                'position_size_multiplier': 0,
                'errors': [msg],
            }

        # In normal regime, missing Greeks may execute but capped to NORMAL/1.0.
        greeks_confidence_cap = None
        size_cap = None
        if (not is_high_vol) and greeks_missing:
            greeks_confidence_cap = "NORMAL"
            size_cap = 1.0

        regime_multiplier = votes.get("regime_filter", {}).get("size_multiplier", 1.0)
        try:
            regime_multiplier = float(regime_multiplier)
        except Exception:
            regime_multiplier = 1.0
        regime_multiplier = max(0.0, min(regime_multiplier, 2.0))

        def _apply_caps(decision_dict):
            base_mult = float(decision_dict.get("position_size_multiplier", 1.0))
            decision_dict["position_size_multiplier"] = base_mult * regime_multiplier
            decision_dict["regime_adjusted"] = True
            decision_dict["regime_multiplier"] = regime_multiplier
            if greeks_confidence_cap is None:
                return decision_dict
            if decision_dict.get("confidence") in ("ELITE", "HIGH"):
                decision_dict["confidence"] = "NORMAL"
            if size_cap is not None:
                decision_dict["position_size_multiplier"] = min(
                    float(decision_dict.get("position_size_multiplier", 1.0)),
                    float(size_cap),
                )
            reason = decision_dict.get("reason") or ""
            cap_note = "Caps: Greeks missing (NORMAL regime)"
            if cap_note not in reason:
                decision_dict["reason"] = f"{reason} | {cap_note}"
            return decision_dict

        # HARD VETO check (any agent can block)
        if veto_count > 0:
            veto_reasons = [
                f"{k}: {v.get('reason','UNKNOWN')}"
                for k, v in votes.items()
                if v.get('vote') == 'VETO'
            ]
            return {
                'decision': 'REJECT',
                'confidence': 'N/A',
                'approve_count': approve_count,
                'veto_count': veto_count,
                'caution_count': caution_count,
                'avg_score': round(avg_score, 1),
                'total_agents': total_agents,
                'reason': f"HARD VETOED by {veto_count} agent(s)",
                'veto_reasons': veto_reasons
            }

        # =========================
        # EXECUTION LOGIC
        # =========================

        # PATH 1: All 4 approve (rare!)
        if total_agents == 4 and approve_count == 4:
            return _apply_caps({
                'decision': 'EXECUTE',
                'confidence': 'ELITE',
                'approve_count': approve_count,
                'veto_count': veto_count,
                'caution_count': caution_count,
                'avg_score': round(avg_score, 1),
                'total_agents': total_agents,
                'reason': f'Perfect alignment: All 4 agents approve (Greek {greek_score}, Sentiment {sentiment_score})',
                'position_size_multiplier': 2.0
            })

        # PATH 2: Strong majority (3/4) with Greek + Sentiment
        elif approve_count == 3 and greek_approved and sentiment_approved:
            if (
                greek_score is not None and sentiment_score is not None
                and greek_score >= 75 and sentiment_score >= 60
            ):
                return _apply_caps({
                    'decision': 'EXECUTE',
                    'confidence': 'HIGH',
                    'approve_count': approve_count,
                    'veto_count': veto_count,
                    'caution_count': caution_count,
                    'avg_score': round(avg_score, 1),
                    'total_agents': total_agents,
                    'reason': f'Strong conviction: Greek {greek_score}, Sentiment {sentiment_score}',
                    'position_size_multiplier': 1.5
                })

        # PATH 3: Good majority (3/4) with Greek strong
        elif approve_count == 3 and greek_approved and greek_strong:
            return _apply_caps({
                'decision': 'EXECUTE',
                'confidence': 'NORMAL',
                'approve_count': approve_count,
                'veto_count': veto_count,
                'caution_count': caution_count,
                'avg_score': round(avg_score, 1),
                'total_agents': total_agents,
                'reason': f'Greek conviction {greek_score_str}/100 with 3/4 approval',
                'position_size_multiplier': 1.0
            })

        # PATH 4: 3-agent unanimous (if Sentiment/Regime abstained)
        elif total_agents == 3 and approve_count == 3:
            if greek_score is not None and greek_score >= 75 and decision_score >= 72:
                return _apply_caps({
                    'decision': 'EXECUTE',
                    'confidence': 'NORMAL',
                    'approve_count': approve_count,
                    'veto_count': veto_count,
                    'caution_count': caution_count,
                    'avg_score': round(avg_score, 1),
                    'total_agents': total_agents,
                    'reason': f'Unanimous (3/3): Greek {greek_score}, Avg {decision_score:.1f}',
                    'position_size_multiplier': 1.0
                })

        # PATH 4.5: NORMAL regime execution without Greeks (confidence/size capped)
        elif (not is_high_vol) and greeks_missing:
            portfolio_ok = votes.get('portfolio', {}).get('vote') == 'APPROVE'
            sentiment_vote = votes.get('sentiment', {}).get('vote')
            sentiment_score_local = _score(votes.get('sentiment', {}))
            macro_ok = votes.get('macro', {}).get('vote') in ('APPROVE', 'CAUTION')
            regime_ok = regime_vote not in ('VETO',)
            sector_ok = votes.get('sector_rotation', {}).get('vote') in ('APPROVE', 'ABSTAIN', 'CAUTION')

            if portfolio_ok and macro_ok and regime_ok and sector_ok and sentiment_vote in ('APPROVE', 'CAUTION'):
                if sentiment_score_local is not None and sentiment_score_local >= 60 and approve_count >= 3:
                    return _apply_caps({
                        'decision': 'EXECUTE',
                        'confidence': 'NORMAL',
                        'approve_count': approve_count,
                        'veto_count': veto_count,
                        'caution_count': caution_count,
                        'avg_score': round(avg_score, 1),
                        'total_agents': total_agents,
                        'reason': (
                            f'EXECUTE without Greeks (NORMAL regime): '
                            f'Sentiment {sentiment_score_local}, Portfolio ok, Macro ok'
                        ),
                        'position_size_multiplier': 1.0
                    })

        # --- PATH 5: Lead-agent bounded override (future-proof) ---
        elif greek_approved and greeks_score is not None and greeks_score >= 85 and approve_count >= 2:
            portfolio_ok = votes.get('portfolio', {}).get('vote') == 'APPROVE'

            sentiment_vote = votes.get('sentiment', {}).get('vote')
            sentiment_not_bearish = (
                sentiment_vote in ['APPROVE', 'CAUTION']
                and sentiment_score is not None and sentiment_score >= 45
            )

            regime_vote = votes.get('regime', {}).get('vote')
            regime_not_bearish = regime_vote not in ['VETO']

            if portfolio_ok and sentiment_not_bearish and regime_not_bearish:
                return _apply_caps({
                    'decision': 'EXECUTE',
                    'confidence': 'NORMAL',
                    'approve_count': approve_count,
                    'veto_count': veto_count,
                    'caution_count': caution_count,
                    'avg_score': round(avg_score, 1),
                    'total_agents': total_agents,
                    'reason': (
                        f'Lead-agent override: Greek {greeks_score}/100, '
                        f'Portfolio approved, no bearish agents'
                    ),
                    'position_size_multiplier': 1.0
                })

        # PATH 6: Multi-agent majority for expanded councils (5+ active votes)
        elif total_agents >= 5:
            print(f"DEBUG: Reached PATH 6! total_agents={total_agents}, approve_count={approve_count}, avg_score={avg_score}")
            if approve_count >= 4:
                return _apply_caps({
                    'decision': 'EXECUTE',
                    'confidence': 'HIGH',
                    'approve_count': approve_count,
                    'veto_count': veto_count,
                    'caution_count': caution_count,
                    'avg_score': round(avg_score, 1),
                    'total_agents': total_agents,
                    'reason': f'Multi-agent majority: {approve_count}/{total_agents} approve',
                    'position_size_multiplier': 1.0
                })
            elif approve_count == 3 and avg_score >= 68:
                return _apply_caps({
                    'decision': 'EXECUTE',
                    'confidence': 'NORMAL',
                    'approve_count': approve_count,
                    'veto_count': veto_count,
                    'caution_count': caution_count,
                    'avg_score': round(avg_score, 1),
                    'total_agents': total_agents,
                    'reason': f'3/{total_agents} approvals with strong average score ({avg_score:.1f})',
                    'position_size_multiplier': 1.0
                })
            elif approve_count == 3 and greek_approved and greek_score is not None and greek_score >= 65:
                return _apply_caps({
                    'decision': 'EXECUTE',
                    'confidence': 'NORMAL',
                    'approve_count': approve_count,
                    'veto_count': veto_count,
                    'caution_count': caution_count,
                    'avg_score': round(avg_score, 1),
                    'total_agents': total_agents,
                    'reason': f'3/{total_agents} approvals with Greek support ({greek_score:.1f})',
                    'position_size_multiplier': 1.0
                })

        # REJECT everything else
        if approve_count >= 2:
            reason = (
                f"Insufficient conviction ({approve_count}/{total_agents} approve, "
                f"Greek {greek_score_str}, Sentiment {sent_score_str})"
            )
        else:
            reason = f'Low approval ({approve_count}/{total_agents})'

        return {
            'decision': 'REJECT',
            'confidence': 'N/A',
            'approve_count': approve_count,
            'veto_count': veto_count,
            'caution_count': caution_count,
            'avg_score': round(avg_score, 1),
            'total_agents': total_agents,
            'reason': reason,
            'position_size_multiplier': 0
        }
    
    def _display_decision(self, decision):
        """Display final council decision"""
        
        print(f"{'='*80}")
        print(f"🏛️  VETO COUNCIL DECISION")
        print(f"{'='*80}\n")
        
        print(f"📊 VOTE TALLY:")
        print(f"  ✅ APPROVE: {decision['approve_count']}")
        print(f"  ⚠️  CAUTION: {decision['caution_count']}")
        print(f"  ❌ VETO: {decision['veto_count']}")
        if 'valid_votes' in decision:
            print(f"  🧮 Valid Votes: {decision['valid_votes']}")
        print(f"  📈 Avg Score: {decision['avg_score']}/100")
        
        result = decision['decision']
        
        if result == 'EXECUTE':
            emoji = "🟢"
            confidence = decision['confidence']
            multiplier = decision['position_size_multiplier']
            print(f"\n{emoji} FINAL DECISION: {result}")
            print(f"  Confidence: {confidence}")
            print(f"  Position Size: {multiplier}x normal")
        elif result == 'CAUTION':
            emoji = "🟡"
            print(f"\n{emoji} FINAL DECISION: {result}")
            print(f"  Consider reducing position size or waiting")
        else:
            emoji = "🔴"
            print(f"\n{emoji} FINAL DECISION: {result}")
            if 'veto_reasons' in decision:
                print(f"\n  Veto Reasons:")
                for reason in decision['veto_reasons']:
                    print(f"    • {reason}")
        
        print(f"\n  Reason: {decision['reason']}")
        print(f"\n{'='*80}\n")
    
    def scan_watchlist(self, tickers):
        """
        Scan entire watchlist through Veto Council
        Returns approved trades only
        """
        
        print(f"\n{'#'*80}")
        print(f"🗳️  VETO COUNCIL WATCHLIST SCAN")
        print(f"{'#'*80}\n")
        print(f"Scanning {len(tickers)} tickers...\n")

        if not self.run_id:
            run_id = self.logger.start_run(
                engine_name="VETO_SCAN",
                notes=f"scan_watchlist {len(tickers)} tickers",
                watchlist_size=len(tickers),
            )
            self.set_run(run_id)
            print(f"ℹ️  Auto-created VETO_SCAN run_id: {run_id}")
        
        approved_trades = []
        
        for ticker in tickers:
            print(f"\n{'─'*80}")
            print(f"Evaluating: {ticker}")
            print(f"{'─'*80}")
            
            try:
                decision = self.evaluate_trade(ticker, show_details=False)
                
                # Quick summary
                result = decision['decision']
                if result == 'EXECUTE':
                    emoji = "🟢"
                    approved_trades.append({
                        'ticker': ticker,
                        'decision': decision
                    })
                elif result == 'CAUTION':
                    emoji = "🟡"
                else:
                    emoji = "🔴"
                
                print(f"{emoji} {ticker}: {result} ({decision['approve_count']}/{decision.get('total_agents', 4)} approve)")
                
            except Exception as e:
                print(f"❌ {ticker}: Error - {e}")
        
        # Summary
        print(f"\n{'='*80}")
        print(f"📊 SCAN SUMMARY")
        print(f"{'='*80}")
        print(f"Total Scanned: {len(tickers)}")
        print(f"✅ Approved: {len(approved_trades)}")
        print(f"❌ Rejected: {len(tickers) - len(approved_trades)}")
        
        if approved_trades:
            print(f"\n🎯 APPROVED TRADES:")
            for trade in approved_trades:
                conf = trade['decision'].get('confidence', 'NORMAL')
                print(f"  • {trade['ticker']} - Confidence: {conf}")
        
        print(f"\n{'='*80}\n")
        
        return approved_trades


def test_veto_council():
    """Test the Veto Council"""
    
    print("🚀 Testing Veto Council - Multi-Agent Decision System\n")
    
    council = VetoCouncil()
    
    # Test on a few tickers
    test_tickers = ["HIMS", "RKLB", "COIN"]
    
    print("\n🧪 DETAILED EVALUATION TEST\n")
    
    for ticker in test_tickers:
        council.evaluate_trade(ticker, show_details=True)
        input("Press ENTER to continue to next ticker...")


if __name__ == "__main__":
    test_veto_council()
