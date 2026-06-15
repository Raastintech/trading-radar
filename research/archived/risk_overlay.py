"""
Risk Overlay System - Institutional Intelligence Layers

Additional intelligence that sits ABOVE your strategies:

1. Earnings Calendar - Don't enter 2 days before earnings
2. Sector Rotation Detector - Follow the hot money
3. Macro Event Calendar - Fed meetings, CPI, NFP
4. Correlation Matrix - Diversification optimizer
5. Black Swan Detector - Unusual volatility events

This is what institutions have TEAMS for.
You're automating it.
"""

from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Set
import numpy as np
import json
# yfinance removed — earnings dates now fetched via FMP
from collections import defaultdict
from dataclasses import dataclass


@dataclass
class EarningsEvent:
    """Upcoming earnings announcement"""
    ticker: str
    date: datetime
    days_until: int
    estimated_eps: Optional[float] = None
    is_before_market: bool = True


@dataclass
class MacroEvent:
    """Major macro economic event"""
    name: str
    date: datetime
    days_until: int
    impact_level: str  # 'HIGH', 'MEDIUM', 'LOW'
    description: str


@dataclass
class SectorPerformance:
    """Sector rotation metrics"""
    sector: str
    return_1d: float
    return_5d: float
    return_20d: float
    relative_strength: float  # vs SPY
    rank: int
    momentum: str  # 'HOT', 'WARM', 'NEUTRAL', 'COLD'


class RiskOverlay:
    """
    Institutional-grade risk intelligence overlay
    
    This sits ABOVE your strategies and provides:
    - Earnings calendar awareness
    - Sector rotation intelligence
    - Macro event tracking
    - Correlation monitoring
    - Black swan detection
    
    Think of this as your research department.
    """
    
    # Sector ETF mapping
    SECTOR_ETFS = {
        'Technology': 'XLK',
        'Financials': 'XLF',
        'Healthcare': 'XLV',
        'Energy': 'XLE',
        'Industrials': 'XLI',
        'Consumer Discretionary': 'XLY',
        'Consumer Staples': 'XLP',
        'Materials': 'XLB',
        'Real Estate': 'XLRE',
        'Utilities': 'XLU',
        'Communications': 'XLC'
    }
    
    # Known macro events (2026 calendar)
    MACRO_CALENDAR_2026 = [
        {'date': '2026-02-06', 'event': 'NFP (Jobs Report)', 'impact': 'HIGH'},
        {'date': '2026-02-11', 'event': 'CPI Report', 'impact': 'HIGH'},
        {'date': '2026-02-18', 'event': 'Fed Meeting', 'impact': 'HIGH'},
        {'date': '2026-03-06', 'event': 'NFP (Jobs Report)', 'impact': 'HIGH'},
        {'date': '2026-03-11', 'event': 'CPI Report', 'impact': 'HIGH'},
        {'date': '2026-03-18', 'event': 'FOMC Meeting', 'impact': 'HIGH'},
        {'date': '2026-04-03', 'event': 'NFP (Jobs Report)', 'impact': 'HIGH'},
        {'date': '2026-04-10', 'event': 'CPI Report', 'impact': 'HIGH'},
        {'date': '2026-04-29', 'event': 'FOMC Meeting', 'impact': 'HIGH'},
    ]
    
    def __init__(self):
        self.earnings_cache = {}
        self.sector_cache = {}
        self.last_update = None
        
        print("🛡️ Risk Overlay System initialized")
        print("   Layers: Earnings | Sectors | Macro | Correlation | Black Swan")
    
    def check_entry_allowed(self, ticker: str, current_date: Optional[datetime] = None) -> Dict:
        """
        Master check - is entry allowed for this ticker?
        
        Args:
            ticker: Stock symbol
            current_date: Date to check (defaults to now)
        
        Returns:
            {
                'allowed': bool,
                'warnings': List[str],
                'blocks': List[str],
                'risk_score': float (0-100)
            }
        """
        
        if current_date is None:
            current_date = datetime.now()
        
        warnings = []
        blocks = []
        risk_score = 0
        
        # CHECK 1: Earnings proximity
        earnings_check = self.check_earnings_proximity(ticker, current_date)
        
        if earnings_check['has_earnings']:
            days = earnings_check['days_until']
            
            if days <= 2:
                blocks.append(f"Earnings in {days} days - TOO RISKY")
                risk_score += 50
            elif days <= 5:
                warnings.append(f"Earnings in {days} days - Use caution")
                risk_score += 25
        
        # CHECK 2: Macro events
        macro_check = self.check_macro_events(current_date)
        
        if macro_check['high_impact_today']:
            warnings.append(f"High-impact event today: {macro_check['events'][0]['name']}")
            risk_score += 15
        
        if macro_check['high_impact_this_week']:
            risk_score += 5
        
        # CHECK 3: Black swan detection
        black_swan = self.detect_black_swan(ticker)
        
        if black_swan['detected']:
            blocks.append(f"Black swan detected: {black_swan['reason']}")
            risk_score += 40
        
        # CHECK 4: Sector health
        sector = self.get_ticker_sector(ticker)
        sector_check = self.check_sector_health(sector)
        
        if sector_check['momentum'] == 'COLD':
            warnings.append(f"Sector {sector} is COLD - Consider avoiding")
            risk_score += 10
        
        # DECISION
        allowed = len(blocks) == 0
        
        return {
            'allowed': allowed,
            'warnings': warnings,
            'blocks': blocks,
            'risk_score': risk_score,
            'earnings': earnings_check,
            'macro': macro_check,
            'black_swan': black_swan,
            'sector': sector_check
        }
    
    def check_earnings_proximity(self, ticker: str, date: Optional[datetime] = None) -> Dict:
        """
        Check if ticker has earnings soon
        
        Returns:
            {
                'has_earnings': bool,
                'date': datetime or None,
                'days_until': int or None,
                'is_risky': bool  # Within 2 days
            }
        """
        
        if date is None:
            date = datetime.now()
        
        try:
            # Earnings calendar via FMP (replaces yfinance)
            from core.fmp_client import get_fmp
            cal = get_fmp().get_earnings_calendar(days_ahead=30)
            match = next((e for e in cal if e.get('symbol', '').upper() == ticker.upper()), None)
            if match and match.get('date'):
                from datetime import datetime as _dt
                earnings_date = _dt.strptime(match['date'], '%Y-%m-%d')
                days_until = (earnings_date - date).days
                return {
                    'has_earnings': True,
                    'date': earnings_date,
                    'days_until': days_until,
                    'is_risky': days_until <= 2,
                }
            return {'has_earnings': False, 'date': None, 'days_until': None, 'is_risky': False}

        except Exception as e:
            # If can't get earnings, assume safe
            return {'has_earnings': False, 'date': None, 'days_until': None, 'is_risky': False}
    
    def check_macro_events(self, date: Optional[datetime] = None) -> Dict:
        """
        Check for major macro events
        
        Returns:
            {
                'high_impact_today': bool,
                'high_impact_this_week': bool,
                'events': List[MacroEvent]
            }
        """
        
        if date is None:
            date = datetime.now()
        
        events = []
        high_impact_today = False
        high_impact_this_week = False
        
        for event_data in self.MACRO_CALENDAR_2026:
            event_date = datetime.strptime(event_data['date'], '%Y-%m-%d')
            days_until = (event_date - date).days
            
            # Only show upcoming events (within 30 days)
            if -1 <= days_until <= 30:
                event = MacroEvent(
                    name=event_data['event'],
                    date=event_date,
                    days_until=days_until,
                    impact_level=event_data['impact'],
                    description=event_data.get('description', '')
                )
                
                events.append(event)
                
                if days_until == 0 and event.impact_level == 'HIGH':
                    high_impact_today = True
                
                if 0 <= days_until <= 7 and event.impact_level == 'HIGH':
                    high_impact_this_week = True
        
        return {
            'high_impact_today': high_impact_today,
            'high_impact_this_week': high_impact_this_week,
            'events': events
        }
    
    def detect_black_swan(self, ticker: str) -> Dict:
        """
        Detect black swan events (unusual volatility/volume)
        
        Returns:
            {
                'detected': bool,
                'reason': str,
                'severity': str  # 'LOW', 'MEDIUM', 'HIGH'
            }
        """
        
        try:
            # Get recent data
            stock = yf.Ticker(ticker)
            hist = stock.history(period='5d')
            
            if hist.empty or len(hist) < 2:
                return {'detected': False, 'reason': '', 'severity': 'LOW'}
            
            # Check for unusual gaps
            latest = hist.iloc[-1]
            prev = hist.iloc[-2]
            
            gap = abs(latest['Open'] - prev['Close']) / prev['Close']
            
            # Black swan indicators
            unusual_gap = gap > 0.10  # 10% gap
            circuit_breaker = gap > 0.20  # 20% gap (circuit breaker level)
            
            # Volume spike
            avg_volume = hist['Volume'].iloc[:-1].mean()
            latest_volume = latest['Volume']
            volume_spike = latest_volume > avg_volume * 3
            
            # Detect
            if circuit_breaker:
                return {
                    'detected': True,
                    'reason': f'{gap*100:.1f}% gap - Circuit breaker level',
                    'severity': 'HIGH'
                }
            
            elif unusual_gap and volume_spike:
                return {
                    'detected': True,
                    'reason': f'{gap*100:.1f}% gap with volume spike',
                    'severity': 'MEDIUM'
                }
            
            elif unusual_gap:
                return {
                    'detected': True,
                    'reason': f'{gap*100:.1f}% gap overnight',
                    'severity': 'LOW'
                }
            
            return {'detected': False, 'reason': '', 'severity': 'LOW'}
            
        except Exception as e:
            return {'detected': False, 'reason': '', 'severity': 'LOW'}
    
    def get_sector_rotation(self) -> List[SectorPerformance]:
        """
        Calculate sector rotation rankings
        
        Returns:
            List of sectors sorted by momentum (HOT to COLD)
        """
        
        print("📊 Calculating sector rotation...")
        
        sectors = []
        
        try:
            # Get SPY for relative strength
            spy = yf.Ticker('SPY')
            spy_hist = spy.history(period='1mo')
            
            if spy_hist.empty:
                return []
            
            spy_return_1d = (spy_hist['Close'].iloc[-1] - spy_hist['Close'].iloc[-2]) / spy_hist['Close'].iloc[-2] * 100
            spy_return_5d = (spy_hist['Close'].iloc[-1] - spy_hist['Close'].iloc[-6]) / spy_hist['Close'].iloc[-6] * 100
            spy_return_20d = (spy_hist['Close'].iloc[-1] - spy_hist['Close'].iloc[0]) / spy_hist['Close'].iloc[0] * 100
            
            # Calculate each sector
            for sector_name, etf_ticker in self.SECTOR_ETFS.items():
                try:
                    etf = yf.Ticker(etf_ticker)
                    hist = etf.history(period='1mo')
                    
                    if hist.empty or len(hist) < 20:
                        continue
                    
                    # Calculate returns
                    return_1d = (hist['Close'].iloc[-1] - hist['Close'].iloc[-2]) / hist['Close'].iloc[-2] * 100
                    return_5d = (hist['Close'].iloc[-1] - hist['Close'].iloc[-6]) / hist['Close'].iloc[-6] * 100
                    return_20d = (hist['Close'].iloc[-1] - hist['Close'].iloc[0]) / hist['Close'].iloc[0] * 100
                    
                    # Relative strength vs SPY
                    rel_strength = return_20d - spy_return_20d
                    
                    # Determine momentum
                    if return_5d > 2.0 and rel_strength > 1.0:
                        momentum = 'HOT'
                    elif return_5d > 0 and rel_strength > 0:
                        momentum = 'WARM'
                    elif return_5d < -2.0 and rel_strength < -1.0:
                        momentum = 'COLD'
                    else:
                        momentum = 'NEUTRAL'
                    
                    sectors.append(SectorPerformance(
                        sector=sector_name,
                        return_1d=return_1d,
                        return_5d=return_5d,
                        return_20d=return_20d,
                        relative_strength=rel_strength,
                        rank=0,  # Will set after sorting
                        momentum=momentum
                    ))
                    
                except Exception as e:
                    continue
            
            # Sort by relative strength
            sectors.sort(key=lambda x: x.relative_strength, reverse=True)
            
            # Assign ranks
            for i, sector in enumerate(sectors):
                sector.rank = i + 1
            
            return sectors
            
        except Exception as e:
            print(f"⚠️  Error calculating sector rotation: {e}")
            return []
    
    def check_sector_health(self, sector: str) -> Dict:
        """
        Check health of specific sector
        
        Returns:
            {
                'sector': str,
                'momentum': str,
                'rank': int,
                'should_trade': bool
            }
        """
        
        # Get current sector rotation
        rotation = self.get_sector_rotation()
        
        if not rotation:
            return {
                'sector': sector,
                'momentum': 'NEUTRAL',
                'rank': 6,
                'should_trade': True
            }
        
        # Find sector
        for sector_perf in rotation:
            if sector_perf.sector == sector:
                return {
                    'sector': sector,
                    'momentum': sector_perf.momentum,
                    'rank': sector_perf.rank,
                    'relative_strength': sector_perf.relative_strength,
                    'should_trade': sector_perf.momentum in ['HOT', 'WARM', 'NEUTRAL']
                }
        
        return {
            'sector': sector,
            'momentum': 'NEUTRAL',
            'rank': 6,
            'should_trade': True
        }
    
    def get_ticker_sector(self, ticker: str) -> str:
        """Get sector for a ticker"""
        
        # Hardcoded mapping for common tickers
        SECTOR_MAP = {
            'NVDA': 'Technology',
            'AMD': 'Technology',
            'TSLA': 'Consumer Discretionary',
            'AAPL': 'Technology',
            'MSFT': 'Technology',
            'GOOGL': 'Communications',
            'META': 'Communications',
            'AMZN': 'Consumer Discretionary',
            'COIN': 'Financials',
            'RKLB': 'Industrials',
            'ASTS': 'Communications',
            'PLTR': 'Technology',
            'SOFI': 'Financials',
            'HOOD': 'Financials'
        }
        
        return SECTOR_MAP.get(ticker, 'Technology')
    
    def calculate_correlation_matrix(self, tickers: List[str], days: int = 60) -> Dict[str, Dict[str, float]]:
        """
        Calculate correlation matrix for portfolio diversification
        
        Returns:
            {
                'TICKER1': {'TICKER2': 0.85, 'TICKER3': 0.45},
                'TICKER2': {'TICKER1': 0.85, 'TICKER3': 0.72}
            }
        """
        
        print(f"📊 Calculating correlations for {len(tickers)} tickers...")
        
        try:
            # Get historical data
            data = {}
            
            for ticker in tickers:
                try:
                    stock = yf.Ticker(ticker)
                    hist = stock.history(period=f'{days}d')
                    
                    if not hist.empty:
                        data[ticker] = hist['Close'].pct_change().dropna()
                except:
                    continue
            
            # Calculate correlations
            correlations = {}
            
            for ticker1 in data:
                correlations[ticker1] = {}
                
                for ticker2 in data:
                    if ticker1 != ticker2:
                        try:
                            # Align dates
                            common_dates = data[ticker1].index.intersection(data[ticker2].index)
                            
                            if len(common_dates) > 10:
                                corr = np.corrcoef(
                                    data[ticker1].loc[common_dates],
                                    data[ticker2].loc[common_dates]
                                )[0, 1]
                                
                                correlations[ticker1][ticker2] = corr
                        except:
                            continue
            
            return correlations
            
        except Exception as e:
            print(f"⚠️  Error calculating correlations: {e}")
            return {}
    
    def check_portfolio_diversification(self, positions: List[str]) -> Dict:
        """
        Check if portfolio is well diversified
        
        Returns:
            {
                'is_diversified': bool,
                'warnings': List[str],
                'avg_correlation': float,
                'max_correlation': float,
                'concentration_risk': str
            }
        """
        
        if len(positions) < 2:
            return {
                'is_diversified': True,
                'warnings': [],
                'avg_correlation': 0.0,
                'max_correlation': 0.0,
                'concentration_risk': 'LOW'
            }
        
        # Calculate correlations
        corr_matrix = self.calculate_correlation_matrix(positions)
        
        if not corr_matrix:
            return {
                'is_diversified': True,
                'warnings': ['Could not calculate correlations'],
                'avg_correlation': 0.0,
                'max_correlation': 0.0,
                'concentration_risk': 'UNKNOWN'
            }
        
        # Extract all correlation values
        all_corrs = []
        for ticker1 in corr_matrix:
            for ticker2, corr in corr_matrix[ticker1].items():
                all_corrs.append(corr)
        
        if not all_corrs:
            avg_corr = 0.0
            max_corr = 0.0
        else:
            avg_corr = np.mean(all_corrs)
            max_corr = np.max(all_corrs)
        
        # Warnings
        warnings = []
        
        if avg_corr > 0.7:
            warnings.append(f"High average correlation: {avg_corr:.2f} - Portfolio not diversified")
        
        if max_corr > 0.9:
            warnings.append(f"Two positions highly correlated: {max_corr:.2f}")
        
        # Concentration risk
        if avg_corr > 0.7:
            concentration = 'HIGH'
        elif avg_corr > 0.5:
            concentration = 'MEDIUM'
        else:
            concentration = 'LOW'
        
        is_diversified = len(warnings) == 0
        
        return {
            'is_diversified': is_diversified,
            'warnings': warnings,
            'avg_correlation': avg_corr,
            'max_correlation': max_corr,
            'concentration_risk': concentration
        }
    
    def generate_risk_report(self, positions: List[str], current_date: Optional[datetime] = None) -> str:
        """
        Generate comprehensive risk report
        
        Returns formatted string
        """
        
        if current_date is None:
            current_date = datetime.now()
        
        report = []
        
        report.append("=" * 80)
        report.append("🛡️ RISK OVERLAY REPORT")
        report.append("=" * 80)
        report.append("")
        
        # Macro events
        report.append("📅 MACRO EVENTS (Next 30 Days):")
        macro = self.check_macro_events(current_date)
        
        if macro['events']:
            for event in macro['events'][:5]:
                report.append(f"   {event.date.strftime('%Y-%m-%d')}: {event.name} ({event.impact_level} impact)")
        else:
            report.append("   No major events scheduled")
        
        report.append("")
        
        # Sector rotation
        report.append("📊 SECTOR ROTATION:")
        sectors = self.get_sector_rotation()
        
        if sectors:
            report.append("   Rank  Sector                    5D    20D   Momentum")
            report.append("   " + "-" * 60)
            
            for sector in sectors[:5]:
                report.append(f"   #{sector.rank:<4} {sector.sector:<24} {sector.return_5d:+5.1f}% {sector.return_20d:+5.1f}%  {sector.momentum}")
        
        report.append("")
        
        # Position checks
        if positions:
            report.append("🎯 POSITION RISK CHECKS:")
            
            for ticker in positions:
                check = self.check_entry_allowed(ticker, current_date)
                
                status = "✅ CLEAR" if check['allowed'] else "🚫 BLOCKED"
                risk = check['risk_score']
                
                report.append(f"   {ticker}: {status} (Risk: {risk}/100)")
                
                if check['blocks']:
                    for block in check['blocks']:
                        report.append(f"      🚫 {block}")
                
                if check['warnings']:
                    for warning in check['warnings']:
                        report.append(f"      ⚠️  {warning}")
            
            report.append("")
            
            # Diversification
            div_check = self.check_portfolio_diversification(positions)
            
            report.append("🔀 PORTFOLIO DIVERSIFICATION:")
            report.append(f"   Avg Correlation: {div_check['avg_correlation']:.2f}")
            report.append(f"   Max Correlation: {div_check['max_correlation']:.2f}")
            report.append(f"   Concentration Risk: {div_check['concentration_risk']}")
            
            if div_check['warnings']:
                for warning in div_check['warnings']:
                    report.append(f"   ⚠️  {warning}")
        
        report.append("")
        report.append("=" * 80)
        
        return "\n".join(report)


# =============================================================================
# Command Line Interface
# =============================================================================

def main():
    """Test risk overlay"""
    
    print("\n" + "="*80)
    print("🛡️ RISK OVERLAY SYSTEM - TEST")
    print("="*80 + "\n")
    
    overlay = RiskOverlay()
    
    # Test 1: Check single ticker
    print("TEST 1: Entry Check for NVDA")
    print("-" * 40)
    
    result = overlay.check_entry_allowed('NVDA')
    
    print(f"Allowed: {result['allowed']}")
    print(f"Risk Score: {result['risk_score']}/100")
    
    if result['warnings']:
        print("\nWarnings:")
        for w in result['warnings']:
            print(f"  ⚠️  {w}")
    
    if result['blocks']:
        print("\nBlocks:")
        for b in result['blocks']:
            print(f"  🚫 {b}")
    
    print("\n" + "-" * 40)
    
    # Test 2: Sector rotation
    print("\nTEST 2: Sector Rotation")
    print("-" * 40)
    
    sectors = overlay.get_sector_rotation()
    
    if sectors:
        print("\nTop 5 Sectors:")
        for i, sector in enumerate(sectors[:5], 1):
            print(f"{i}. {sector.sector}: {sector.return_5d:+.2f}% (5D) | {sector.momentum}")
    
    print("\n" + "-" * 40)
    
    # Test 3: Full report
    print("\nTEST 3: Full Risk Report")
    print("-" * 40)
    
    positions = ['NVDA', 'AMD', 'TSLA']
    report = overlay.generate_risk_report(positions)
    print("\n" + report)


if __name__ == "__main__":
    main()