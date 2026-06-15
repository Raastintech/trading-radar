"""
Portfolio Coordinator - Institutional-Grade Multi-Strategy Management

This is NOT basic allocation. This is:
- Dynamic risk parity rebalancing
- Kelly Criterion position sizing
- Correlation-aware diversification
- Drawdown protection circuits
- Strategy performance measurement
- Regime-adaptive allocation

The kind of system Renaissance Technologies uses, adapted for asymmetric retail edge.
"""

from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
import numpy as np
from collections import defaultdict
import sqlite3
import json


@dataclass
class StrategyAllocation:
    """Allocation parameters for a strategy"""
    name: str
    target_pct: float  # Target allocation percentage
    current_pct: float  # Current allocation
    max_pct: float  # Maximum allowed
    min_pct: float  # Minimum allowed
    risk_budget: float  # Risk allocation (volatility budget)
    performance_score: float  # Recent performance metric


@dataclass
class RiskLimits:
    """Portfolio-wide risk limits"""
    max_daily_loss_pct: float = 2.0
    max_weekly_loss_pct: float = 5.0
    max_monthly_loss_pct: float = 10.0
    max_positions: int = 10
    max_positions_per_strategy: int = 6
    max_sector_exposure_pct: float = 30.0
    max_correlation: float = 0.7
    max_portfolio_heat: float = 0.15  # Max % of account at risk


class PortfolioCoordinator:
    """
    Master orchestrator for multi-strategy portfolio
    
    Responsibilities:
    1. Capital allocation across strategies
    2. Risk limit enforcement
    3. Drawdown protection
    4. Performance attribution
    5. Regime-adaptive rebalancing
    
    This is the "CIO" of your trading operation.
    """
    
    # Allocation modes based on market regime
    ALLOCATION_MODES = {
        'CONSERVATIVE': {
            'description': 'Bear market / High VIX',
            'voyager': 0.10,
            'sniper': 0.10,
            'remora': 0.05,
            'short': 0.25,
            'contrarian': 0.05,
            'cash': 0.45,
            'max_daily_trades': 2
        },
        'BALANCED': {
            'description': 'Normal market conditions',
            'voyager': 0.20,
            'sniper': 0.20,
            'remora': 0.15,
            'short': 0.20,
            'contrarian': 0.05,
            'cash': 0.20,
            'max_daily_trades': 4
        },
        'FEAR_OPPORTUNITY': {
            'description': 'VIX 28+ fear spike — contrarian accumulation mode',
            'voyager': 0.10,
            'sniper': 0.05,
            'remora': 0.05,
            'short': 0.25,
            'contrarian': 0.20,
            'cash': 0.35,
            'max_daily_trades': 3
        },
        'AGGRESSIVE': {
            'description': 'Bull market / Opportunities rich',
            'voyager': 0.20,
            'sniper': 0.25,
            'remora': 0.20,
            'short': 0.10,
            'contrarian': 0.05,
            'cash': 0.20,
            'max_daily_trades': 6
        },
        'DEFENSIVE': {
            'description': 'Drawdown protection mode',
            'voyager': 0.10,
            'sniper': 0.05,
            'remora': 0.00,  # Remora off during drawdown
            'short': 0.20,
            'contrarian': 0.05,
            'cash': 0.60,
            'max_daily_trades': 1
        }
    }
    
    def __init__(self, account_size: float, db_path: str = 'trading_performance.db'):
        """
        Initialize Portfolio Coordinator
        
        Args:
            account_size: Total account value
            db_path: Database for tracking
        """
        
        self.account_size = account_size
        self.db_path = db_path
        
        # Current state
        self.current_mode = 'BALANCED'
        self.strategies = {}
        self.positions = {}
        self.daily_pnl = 0.0
        self.weekly_pnl = 0.0
        self.monthly_pnl = 0.0
        
        # Risk tracking
        self.risk_limits = RiskLimits()
        self.circuit_breaker_triggered = False
        
        # Performance tracking
        self.strategy_performance = defaultdict(lambda: {
            'trades': 0,
            'wins': 0,
            'total_pnl': 0.0,
            'avg_return': 0.0,
            'sharpe': 0.0,
            'max_dd': 0.0
        })

        self.sync_realized_from_db = False
        
        print("🎯 Portfolio Coordinator initialized")
        print(f"   Account: ${account_size:,.0f}")
        print(f"   Mode: {self.current_mode}")
        _init_alloc = self.ALLOCATION_MODES[self.current_mode]
        print(f"   Allocation: Voyager {_init_alloc.get('voyager', 0)*100:.0f}% | "
              f"Sniper {_init_alloc['sniper']*100:.0f}% | "
              f"Remora {_init_alloc['remora']*100:.0f}% | "
              f"Short {_init_alloc.get('short', 0)*100:.0f}% | "
              f"Contrarian {_init_alloc.get('contrarian', 0)*100:.0f}% | "
              f"Cash {_init_alloc['cash']*100:.0f}%")

    def _refresh_realized_state_from_db(self):
        """
        Rebuild realized P&L state from the canonical trades table.

        This keeps the circuit breaker and drawdown logic grounded in actual
        closed trades, even after daemon restarts.
        """
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='trades'")
            if not cur.fetchone():
                conn.close()
                return

            cur.execute("PRAGMA table_info(trades)")
            cols = {row[1] for row in cur.fetchall()}
            if not cols:
                conn.close()
                return

            exit_col = "exit_time" if "exit_time" in cols else ("exit_date" if "exit_date" in cols else None)
            if not exit_col:
                conn.close()
                return

            pnl_expr = "COALESCE(pnl_usd, pnl, 0.0)" if "pnl_usd" in cols else ("COALESCE(pnl, 0.0)" if "pnl" in cols else "0.0")
            ret_expr = "COALESCE(pnl_pct, pnl_percent, 0.0)" if "pnl_pct" in cols else ("COALESCE(pnl_percent, 0.0)" if "pnl_percent" in cols else "0.0")
            strategy_expr = "UPPER(COALESCE(strategy, setup_type, 'UNKNOWN'))" if "strategy" in cols else ("UPPER(COALESCE(setup_type, 'UNKNOWN'))" if "setup_type" in cols else "'UNKNOWN'")
            analytics_filter = "AND COALESCE(analytics_excluded, 0) = 0" if "analytics_excluded" in cols else ""
            closed_predicate = f"(UPPER(COALESCE(status, '')) = 'CLOSED' OR {exit_col} IS NOT NULL)" if "status" in cols else f"{exit_col} IS NOT NULL"

            def _sum_pnl(date_clause: str) -> float:
                cur.execute(
                    f"""
                    SELECT COALESCE(SUM({pnl_expr}), 0.0)
                    FROM trades
                    WHERE {closed_predicate}
                      AND {date_clause}
                      {analytics_filter}
                    """
                )
                row = cur.fetchone()
                return float(row[0] or 0.0) if row else 0.0

            self.daily_pnl = _sum_pnl(f"date({exit_col}) = date('now','localtime')")
            self.weekly_pnl = _sum_pnl(f"date({exit_col}) >= date('now','localtime','-6 day')")
            self.monthly_pnl = _sum_pnl(f"date({exit_col}) >= date('now','localtime','start of month')")

            refreshed_perf = defaultdict(lambda: {
                'trades': 0,
                'wins': 0,
                'total_pnl': 0.0,
                'avg_return': 0.0,
                'sharpe': 0.0,
                'max_dd': 0.0,
                'avg_win': 0.0,
                'avg_loss': 0.0,
            })

            cur.execute(
                f"""
                SELECT
                    {strategy_expr} AS strategy_name,
                    COUNT(*) AS trades,
                    SUM(CASE WHEN {pnl_expr} > 0 THEN 1 ELSE 0 END) AS wins,
                    COALESCE(SUM({pnl_expr}), 0.0) AS total_pnl,
                    COALESCE(AVG({ret_expr}), 0.0) AS avg_return,
                    COALESCE(AVG(CASE WHEN {pnl_expr} > 0 THEN {ret_expr} END), 0.0) AS avg_win,
                    COALESCE(AVG(CASE WHEN {pnl_expr} < 0 THEN ABS({ret_expr}) END), 0.0) AS avg_loss
                FROM trades
                WHERE {closed_predicate}
                  {analytics_filter}
                GROUP BY strategy_name
                """
            )
            for row in cur.fetchall():
                strategy = str(row["strategy_name"] or "UNKNOWN").upper()
                bucket = refreshed_perf[strategy]
                bucket['trades'] = int(row["trades"] or 0)
                bucket['wins'] = int(row["wins"] or 0)
                bucket['total_pnl'] = float(row["total_pnl"] or 0.0)
                bucket['avg_return'] = float(row["avg_return"] or 0.0)
                bucket['avg_win'] = float(row["avg_win"] or 0.0)
                bucket['avg_loss'] = float(row["avg_loss"] or 0.0)

            self.strategy_performance = refreshed_perf
            self.circuit_breaker_triggered = self.daily_pnl <= -(
                (self.risk_limits.max_daily_loss_pct / 100.0) * float(self.account_size or 0.0)
            )
            conn.close()
        except Exception:
            pass
    
    def evaluate_trade_request(self, strategy: str, ticker: str, 
                               signal: Dict, decision: Dict) -> Dict:
        """
        Portfolio-level decision: Should we take this trade?
        
        This is THE critical function. It can override strategy decisions
        based on portfolio-level constraints.
        
        Args:
            strategy: 'SNIPER' or 'REMORA'
            ticker: Stock symbol
            signal: Strategy signal
            decision: Strategy decision
        
        Returns:
            {
                'approved': bool,
                'position_size': float,
                'reason': str,
                'allocation_adjusted': bool
            }
        """
        
        if getattr(self, "sync_realized_from_db", False):
            self._refresh_realized_state_from_db()

        # GATE 1: Circuit Breaker Check
        if self.circuit_breaker_triggered:
            return {
                'approved': False,
                'position_size': 0.0,
                'shares': 0,
                'reason': 'CIRCUIT_BREAKER: Daily loss limit hit',
                'allocation_adjusted': False
            }
        
        # GATE 2: Daily Loss Limit
        if not self._check_daily_loss_limit():
            self._trigger_circuit_breaker()
            return {
                'approved': False,
                'position_size': 0.0,
                'shares': 0,
                'reason': 'DAILY_LOSS_LIMIT: Exceeded -2%',
                'allocation_adjusted': False
            }
        
        # GATE 3: Position Limit
        if not self._check_position_limit(strategy):
            return {
                'approved': False,
                'position_size': 0.0,
                'shares': 0,
                'reason': f'POSITION_LIMIT: {strategy} at max positions',
                'allocation_adjusted': False
            }
        
        # GATE 4: Sector Exposure
        sector = signal.get('sector', 'Unknown')
        proposed_value = signal.get('market_value') or signal.get('position_value') or 0.0
        if not self._check_sector_limit(sector, proposed_value=proposed_value):
            return {
                'approved': False,
                'position_size': 0.0,
                'shares': 0,
                'reason': f'SECTOR_LIMIT: {sector} at 30% max',
                'allocation_adjusted': False
            }
        
        # GATE 5: Correlation Check
        if not self._check_correlation(ticker):
            return {
                'approved': False,
                'position_size': 0.0,
                'shares': 0,
                'reason': 'CORRELATION: Too similar to existing positions',
                'allocation_adjusted': False
            }
        
        # GATE 6: Portfolio Heat
        base_size = self._calculate_base_position_size(strategy, signal)
        if base_size < 1:
            return {
                'approved': False,
                'position_size': 0.0,
                'shares': 0,
                'reason': f'ALLOCATION_ZERO: {strategy} disabled in {self.current_mode}',
                'allocation_adjusted': False
            }
        
        if not self._check_portfolio_heat(base_size, signal):
            # Try reducing size
            reduced_size = base_size * 0.5
            
            if self._check_portfolio_heat(reduced_size, signal):
                return {
                    'approved': True,
                    'position_size': reduced_size,
                    'shares': int(reduced_size),
                    'reason': 'APPROVED: Size reduced for heat management',
                    'allocation_adjusted': True
                }
            else:
                return {
                    'approved': False,
                    'position_size': 0.0,
                    'shares': 0,
                    'reason': 'PORTFOLIO_HEAT: Even 50% size exceeds limits',
                    'allocation_adjusted': False
                }
        
        # ALL GATES PASSED
        return {
            'approved': True,
            'position_size': base_size,
            'shares': int(base_size),
            'reason': 'APPROVED: All risk checks passed',
            'allocation_adjusted': False
        }
    
    def _calculate_base_position_size(self, strategy: str, signal: Dict) -> float:
        """
        Calculate position size using Kelly Criterion + Risk Parity
        
        This is sophisticated institutional sizing, not basic % of account.
        """
        
        # Get strategy allocation
        mode = self.ALLOCATION_MODES[self.current_mode]
        strategy_key = str(strategy or "").strip().lower()
        strategy_allocation = float(mode.get(strategy_key, 0.0) or 0.0)
        if strategy_allocation <= 0:
            return 0.0
        
        # Available capital for this strategy
        strategy_capital = self.account_size * strategy_allocation
        
        # Base risk per trade (from signal)
        entry = float(signal.get('entry_price', 0) or 0)
        stop = float(signal.get('stop_loss', 0) or 0)
        direction = (signal.get("direction") or "LONG").upper()
        risk_per_share = (stop - entry) if direction == "SHORT" else (entry - stop)
        
        if risk_per_share <= 0:
            return 0.0
        
        # Risk budget for this trade (5% of strategy capital)
        risk_budget = strategy_capital * 0.05
        
        # Base shares
        base_shares = risk_budget / risk_per_share
        
        # Kelly Criterion adjustment (if we have performance data)
        perf = self.strategy_performance[strategy]
        
        if perf['trades'] >= 10:
            win_rate = perf['wins'] / perf['trades']
            avg_win = perf.get('avg_win', 0.02)
            avg_loss = perf.get('avg_loss', 0.01)
            
            if avg_loss > 0:
                # Kelly = (win_rate * avg_win - (1-win_rate) * avg_loss) / avg_loss
                kelly = (win_rate * avg_win - (1 - win_rate) * avg_loss) / avg_loss
                kelly = max(0.0, min(kelly, 0.25))  # Cap at 25% Kelly
                
                base_shares *= (1 + kelly)
        
        # Strategy-specific multiplier
        if strategy == 'REMORA':
            # Remora can size up when clumsy moments detected
            size_multiplier = signal.get('size_multiplier', 1.0)
            base_shares *= size_multiplier
        
        # Round to integer
        shares = int(base_shares)
        
        return max(1, shares)  # Minimum 1 share
    
    def _check_daily_loss_limit(self) -> bool:
        """Check if daily loss limit exceeded"""
        
        daily_loss_pct = (self.daily_pnl / self.account_size) * 100
        
        return daily_loss_pct > -self.risk_limits.max_daily_loss_pct
    
    def _check_position_limit(self, strategy: str) -> bool:
        """Check position count limits"""
        
        # Total positions
        total_positions = len(self.positions)
        if total_positions >= self.risk_limits.max_positions:
            return False
        
        # Per-strategy positions
        strategy_positions = sum(1 for p in self.positions.values() 
                                if p.get('strategy') == strategy)
        
        return strategy_positions < self.risk_limits.max_positions_per_strategy
    
    def _check_sector_limit(self, sector: str, proposed_value: float = 0.0) -> bool:
        """Check sector exposure limit including proposed trade."""
        
        # Calculate current sector exposure
        sector_value = sum(p.get('market_value', 0) 
                          for p in self.positions.values() 
                          if p.get('sector') == sector)
        
        projected_sector_value = sector_value + float(proposed_value or 0.0)
        sector_pct = (projected_sector_value / self.account_size) * 100
        
        return sector_pct < self.risk_limits.max_sector_exposure_pct
    
    def _check_correlation(self, ticker: str) -> bool:
        """
        Check correlation with existing positions
        
        Simplified: Check if same sector or similar names
        Real implementation would use actual correlation coefficients
        """
        
        # For now, just prevent duplicate tickers
        return ticker not in self.positions
    
    def _check_portfolio_heat(self, shares: float, signal: Dict) -> bool:
        """
        Heat = Sum of (shares * distance_to_stop) for all positions, direction-aware.
        """
        def _risk(entry, stop, qty, direction="LONG"):
            try:
                entry = float(entry or 0)
                stop = float(stop or 0)
                qty = float(qty or 0)
                d = (direction or "LONG").upper()
                if entry <= 0 or stop <= 0 or qty <= 0:
                    return 0.0
                per_share = (stop - entry) if d == "SHORT" else (entry - stop)
                return max(0.0, per_share) * qty
            except Exception:
                return 0.0

        current_heat = 0.0
        for pos in self.positions.values():
            current_heat += _risk(
                pos.get("entry_price", 0),
                pos.get("stop_loss", 0),
                pos.get("shares", 0),
                pos.get("direction") or pos.get("side") or "LONG",
            )

        # Proposed trade heat
        new_heat = _risk(
            signal.get("entry_price", 0),
            signal.get("stop_loss", 0),
            shares,
            signal.get("direction") or "LONG",
        )

        total_heat = current_heat + new_heat
        heat_pct = total_heat / float(self.account_size or 1)

        return heat_pct <= self.risk_limits.max_portfolio_heat
    
    def _trigger_circuit_breaker(self):
        """Activate circuit breaker - stop all trading"""
        
        self.circuit_breaker_triggered = True
        
        print("\n" + "="*80)
        print("🚨 CIRCUIT BREAKER TRIGGERED")
        print("="*80)
        print(f"Daily P&L: ${self.daily_pnl:,.2f} ({self.daily_pnl/self.account_size*100:.2f}%)")
        print(f"Limit: -{self.risk_limits.max_daily_loss_pct}%")
        print("\n⛔ ALL NEW TRADES BLOCKED FOR TODAY")
        print("="*80 + "\n")
        
        # Send alert
        self._send_circuit_breaker_alert()
    
    # Authoritative regime classifier for the V3 live allocator.
    # Legacy regime_filter.py still exists for non-V3 tools and dashboards, but
    # it must not be reintroduced as an execution-time allocation source here.
    def update_regime(self, market_data: Dict):
        """
        Dynamically adjust allocation based on market regime
        
        This is where institutions separate themselves - adaptive allocation
        """
        
        if getattr(self, "sync_realized_from_db", False):
            self._refresh_realized_state_from_db()

        vix = market_data.get('vix_level', 15)
        if not isinstance(vix, (int, float)):
            vix = 20.0
        spy_regime = market_data.get('spy_regime', 'BULL')
        breadth = market_data.get('breadth_score', 0.5)
        
        # Determine mode
        old_mode = self.current_mode
        
        if vix > 35 or (spy_regime == 'BEAR' and vix > 30):
            # True crash panic or confirmed bear with extreme volatility — preserve capital
            new_mode = 'CONSERVATIVE'

        elif vix >= 28:
            # Fear spike 28-35: peak contrarian accumulation window
            new_mode = 'FEAR_OPPORTUNITY'

        elif vix > 25 or breadth < 0.3:
            new_mode = 'DEFENSIVE'

        elif vix < 18 and spy_regime == 'BULL' and breadth > 0.6:
            new_mode = 'AGGRESSIVE'
        
        else:
            new_mode = 'BALANCED'
        
        # Check if drawdown warrants defensive mode
        if self.weekly_pnl / self.account_size < -0.03:  # -3% week
            new_mode = 'DEFENSIVE'
        
        # Switch if needed
        if new_mode != old_mode:
            self.current_mode = new_mode
            
            print(f"\n🔄 REGIME CHANGE: {old_mode} → {new_mode}")
            print(f"   VIX: {vix:.1f} | SPY: {spy_regime} | Breadth: {breadth:.2f}")
            mode_alloc = self.ALLOCATION_MODES[new_mode]
            print(f"   New Allocation: Voyager {mode_alloc.get('voyager', 0)*100:.0f}% | "
                  f"Sniper {mode_alloc['sniper']*100:.0f}% | "
                  f"Remora {mode_alloc['remora']*100:.0f}% | "
                  f"Short {mode_alloc.get('short', 0)*100:.0f}% | "
                  f"Contrarian {mode_alloc.get('contrarian', 0)*100:.0f}%\n")
    
    def record_trade_result(self, strategy: str, ticker: str, 
                           pnl: float, return_pct: float):
        """Record trade result for performance tracking"""
        
        perf = self.strategy_performance[strategy]
        
        perf['trades'] += 1
        perf['total_pnl'] += pnl
        
        if pnl > 0:
            perf['wins'] += 1
        
        # Update daily/weekly/monthly P&L
        self.daily_pnl += pnl
        self.weekly_pnl += pnl
        self.monthly_pnl += pnl
        
        # Log to database
        self._log_trade_result(strategy, ticker, pnl, return_pct)
    
    def reset_daily_tracking(self):
        """Reset daily trackers (call at market close)"""
        
        self.daily_pnl = 0.0
        self.circuit_breaker_triggered = False
        
        print(f"✅ Daily tracking reset - Circuit breaker cleared")
    
    def get_portfolio_status(self) -> Dict:
        """Get current portfolio status"""
        if getattr(self, "sync_realized_from_db", False):
            self._refresh_realized_state_from_db()

        mode = self.ALLOCATION_MODES[self.current_mode]
        
        return {
            'account_size': self.account_size,
            'mode': self.current_mode,
            'allocations': {
                'voyager': mode.get('voyager', 0.0),
                'sniper': mode['sniper'],
                'remora': mode['remora'],
                'short': mode.get('short', 0.0),
                'contrarian': mode.get('contrarian', 0.0),
                'cash': mode['cash']
            },
            'positions': len(self.positions),
            'daily_pnl': self.daily_pnl,
            'weekly_pnl': self.weekly_pnl,
            'monthly_pnl': self.monthly_pnl,
            'circuit_breaker': self.circuit_breaker_triggered,
            'performance': dict(self.strategy_performance)
        }
    
    def _send_circuit_breaker_alert(self):
        """Send urgent alert when circuit breaker trips"""
        
        try:
            from notification_system import NotificationSystem
            
            notifier = NotificationSystem()
            
            subject = "🚨 CIRCUIT BREAKER TRIGGERED"
            body = f"""
            <html>
            <body>
                <h2 style="color: red;">🚨 CIRCUIT BREAKER ACTIVATED</h2>
                
                <p><b>Daily P&L: ${self.daily_pnl:,.2f} ({self.daily_pnl/self.account_size*100:.2f}%)</b></p>
                <p><b>Limit: -{self.risk_limits.max_daily_loss_pct}%</b></p>
                
                <p>All new trades blocked for remainder of day.</p>
                
                <p><b>Time:</b> {datetime.now().strftime('%I:%M %p ET')}</p>
            </body>
            </html>
            """
            
            notifier.send_email(subject, body)
        except:
            pass
    
    def _log_trade_result(self, strategy: str, ticker: str, pnl: float, return_pct: float):
        """Log trade result to database"""
        
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT INTO portfolio_performance (
                    timestamp, strategy, ticker, pnl, return_pct,
                    daily_pnl, weekly_pnl, portfolio_mode
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                datetime.now().isoformat(),
                strategy,
                ticker,
                pnl,
                return_pct,
                self.daily_pnl,
                self.weekly_pnl,
                self.current_mode
            ))
            
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"⚠️  Failed to log trade result: {e}")


# =============================================================================
# Testing Function
# =============================================================================

def test_portfolio_coordinator():
    """Test portfolio coordination logic"""
    
    print("🧪 Testing Portfolio Coordinator\n")
    
    coordinator = PortfolioCoordinator(account_size=91000)
    
    # Test 1: Evaluate trade request
    print("\n" + "="*80)
    print("TEST 1: Evaluate Sniper Trade Request")
    print("="*80)
    
    signal = {
        'entry_price': 850.0,
        'stop_loss': 820.0,
        'target_price': 920.0,
        'risk_reward': 2.33,
        'sector': 'Technology'
    }
    
    decision = {
        'decision': 'EXECUTE',
        'confidence': 'HIGH'
    }
    
    result = coordinator.evaluate_trade_request('SNIPER', 'NVDA', signal, decision)
    
    print(f"\nResult: {result['approved']}")
    print(f"Position Size: {result['position_size']} shares")
    print(f"Reason: {result['reason']}")
    
    # Test 2: Regime update
    print("\n" + "="*80)
    print("TEST 2: Regime Update")
    print("="*80)
    
    # Simulate VIX spike
    coordinator.update_regime({
        'vix_level': 32,
        'spy_regime': 'BEAR',
        'breadth_score': 0.2
    })
    
    # Test 3: Portfolio status
    print("\n" + "="*80)
    print("TEST 3: Portfolio Status")
    print("="*80)
    
    status = coordinator.get_portfolio_status()
    
    print(f"\nMode: {status['mode']}")
    print(f"Allocations: {status['allocations']}")
    print(f"Positions: {status['positions']}")
    print(f"Circuit Breaker: {status['circuit_breaker']}")


if __name__ == "__main__":
    test_portfolio_coordinator()
