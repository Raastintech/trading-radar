"""
SUPERSEDED — moved to core/whale_tracker.py (production version).
This file is kept as a reference / development history only.
Do not import from this file — use core/whale_tracker.py instead.

---

Whale Tracker - Production 13F Implementation

Proper SEC integration using edgartools
Tracks institutional whales by CIK
Compares quarter-over-quarter holdings
Detects accumulation vs distribution

Installation:
pip install edgartools pandas --break-system-packages

Usage:
    tracker = WhaleTracker13F(user_email="Your Name your@email.com")
    activity = tracker.get_whale_activity_for_stock("NVDA")
"""

from typing import Any, Dict, List, Optional
from datetime import datetime
import pandas as pd

# Embedded SEC identity for non-interactive runs
DEFAULT_SEC_IDENTITY = "hedayat.raastin@gmail.com"

# Try to import edgartools
try:
    from edgar import Company, set_identity
    EDGARTOOLS_AVAILABLE = True
except ImportError:
    EDGARTOOLS_AVAILABLE = False


class WhaleTracker13F:
    """
    Production-grade 13F Whale Tracker

    Tracks 20+ major institutional investors (whales)
    Uses SEC EDGAR 13F-HR filings
    Compares Q vs Q-1 to detect accumulation
    """

    # Major institutional investors (CIK numbers)
    # Status as of 2026-04-18 (verified against SEC EDGAR via edgartools):
    #   OK:      CIK resolves to the correct entity with recent filings (2024+)
    #   STALE:   CIK is wrong entity or last filing is old — excluded by staleness check
    #   BROKEN:  CIK has no 13F filings at all — silently skipped
    #   WRONG:   CIK maps to a different institution entirely — needs correction
    MAJOR_WHALES = {
        'Vanguard':              '0000102909',   # OK — VANGUARD GROUP INC
        'BlackRock':             '0001086364',   # STALE — is BLACKROCK ADVISORS LLC, last filing 2016; correct CIK TBD
        'State Street':          '0000093751',   # OK — STATE STREET CORP
        'Fidelity':              '0000315066',   # OK — FMR LLC
        'Berkshire Hathaway':    '0001067983',   # OK — BERKSHIRE HATHAWAY INC
        'ARK Investment':        '0001579982',   # BROKEN — no 13F filings found; CIK needs verification
        'Renaissance Tech':      '0001037389',   # OK — RENAISSANCE TECHNOLOGIES LLC
        'Citadel':               '0001423053',   # OK — CITADEL ADVISORS LLC
        'Two Sigma':             '0001480520',   # BROKEN — no 13F filings found; CIK needs verification
        'DE Shaw':               '0001009207',   # OK — D. E. SHAW & CO., INC.
        'Bridgewater':           '0001350694',   # OK — BRIDGEWATER ASSOCIATES, LP
        'Tiger Global':          '0001167483',   # OK — TIGER GLOBAL MANAGEMENT LLC
        'Millennium Management': '0001359069',   # BROKEN — no 13F filings found; CIK needs verification
        'Point72':               '0001603466',   # OK — POINT72 ASSET MANAGEMENT, L.P.
        'Viking Global':         '0001103804',   # OK — VIKING GLOBAL INVESTORS LP
        'Third Point':           '0001040273',   # OK — THIRD POINT LLC
        'Lone Pine':             '0001061768',   # WRONG — maps to BAUPOST GROUP LLC/MA, not Lone Pine; CIK needs correction
        'Coatue Management':     '0001567892',   # BROKEN — no 13F filings found; CIK needs verification
        'D1 Capital':            '0001769325',   # BROKEN — no 13F filings found; CIK needs verification
        'Soros Fund Management': '0001029160',   # OK — SOROS FUND MANAGEMENT LLC
    }

    # Ticker to company name mapping (for 13F matching)
    TICKER_TO_NAME = {
        'AAPL': 'APPLE',
        'MSFT': 'MICROSOFT',
        'GOOGL': 'ALPHABET',
        'GOOG': 'ALPHABET',
        'AMZN': 'AMAZON',
        'NVDA': 'NVIDIA',
        'META': 'META PLATFORMS',
        'TSLA': 'TESLA',
        'NFLX': 'NETFLIX',
        'AMD': 'ADVANCED MICRO',
        'CRM': 'SALESFORCE',
        'ADBE': 'ADOBE',
        'ORCL': 'ORACLE',
        'CRWD': 'CROWDSTRIKE',
        'SNOW': 'SNOWFLAKE',
        'ZS': 'ZSCALER',
        'DDOG': 'DATADOG',
        'NET': 'CLOUDFLARE',
        'PLTR': 'PALANTIR',
        'ABNB': 'AIRBNB',
        'UBER': 'UBER',
        'DASH': 'DOORDASH',
        'COIN': 'COINBASE',
        'SQ': 'BLOCK',
        'PYPL': 'PAYPAL',
        'SOFI': 'SOFI',
        'HOOD': 'ROBINHOOD',
        'JPM': 'JPMORGAN',
        'BAC': 'BANK OF AMERICA',
        'V': 'VISA',
        'MA': 'MASTERCARD',
        'XOM': 'EXXON MOBIL',
        'CVX': 'CHEVRON',
        'JNJ': 'JOHNSON',
        'UNH': 'UNITEDHEALTH',
        'LLY': 'ELI LILLY',
        'WMT': 'WALMART',
        'PG': 'PROCTER',
        'KO': 'COCA-COLA',
        'SMCI': 'SUPER MICRO',
        'IONQ': 'IONQ',
        'RKLB': 'ROCKET LAB',
    }

    def __init__(self, user_email: str = None):
        """
        Initialize Whale Tracker

        Args:
            user_email: Your email for SEC compliance
                       Format: "Your Name your@email.com"
        """

        if not EDGARTOOLS_AVAILABLE:
            raise ImportError(
                "edgartools not installed\n"
                "Install: pip install edgartools pandas --break-system-packages"
            )

        identity = user_email or DEFAULT_SEC_IDENTITY
        set_identity(identity)
        print(f"✅ SEC Identity: {identity}")

        self.filing_cache: Dict[str, Any] = {}

    def get_whale_activity_for_stock(self, ticker: str, verbose: bool = False) -> Dict:
        """
        Get whale activity for specific stock

        This is the KEY function for Voyager.
        """

        ticker = ticker.upper()

        if verbose:
            print(f"\n🐋 Tracking whale activity: {ticker}")
            print("=" * 60)

        buyers = []
        sellers = []
        holders = []
        total_shares_added = 0

        # Check each major whale
        for whale_name, cik in self.MAJOR_WHALES.items():
            try:
                change = self._get_position_change(cik, ticker, verbose)

                if change:
                    if change['change_type'] == 'INCREASED':
                        buyers.append(
                            {
                                'name': whale_name,
                                'shares_added': change['shares_change'],
                                'change_pct': change['change_pct'],
                            }
                        )
                        total_shares_added += change['shares_change']

                        if verbose:
                            print(
                                f"   📈 {whale_name}: +{change['shares_change']:,} "
                                f"({change['change_pct']:+.1f}%)"
                            )

                    elif change['change_type'] == 'DECREASED':
                        sellers.append(
                            {
                                'name': whale_name,
                                'shares_removed': abs(change['shares_change']),
                                'change_pct': abs(change['change_pct']),
                            }
                        )
                        total_shares_added += change['shares_change']

                        if verbose:
                            print(
                                f"   📉 {whale_name}: {change['shares_change']:,} "
                                f"({change['change_pct']:+.1f}%)"
                            )

                    elif change['change_type'] in ['HOLDING', 'NEW']:
                        holders.append(whale_name)

                        if verbose and change['change_type'] == 'NEW':
                            print(f"   ✨ {whale_name}: NEW position")

            except Exception:
                # Skip whales that don't hold this stock / parsing errors
                continue

        if verbose:
            print("=" * 60)

        # Determine net flow
        if len(buyers) > len(sellers) and total_shares_added > 0:
            net_flow = 'BUYING'
        elif len(sellers) > len(buyers) and total_shares_added < 0:
            net_flow = 'SELLING'
        else:
            net_flow = 'NEUTRAL'

        # Confidence
        total_tracked = len(buyers) + len(sellers) + len(holders)

        if total_tracked >= 5:
            confidence = 'HIGH'
        elif total_tracked >= 3:
            confidence = 'MODERATE'
        else:
            confidence = 'LOW'

        # Sort leaders
        buyers.sort(key=lambda x: x['shares_added'], reverse=True)
        sellers.sort(key=lambda x: x['shares_removed'], reverse=True)

        result = {
            'ticker': ticker,
            'whales_buying': len(buyers),
            'whales_selling': len(sellers),
            'whales_holding': len(holders),
            'net_flow': net_flow,
            'total_shares_added': total_shares_added,
            'top_buyers': buyers[:3],
            'top_sellers': sellers[:3],
            'confidence': confidence,
            'last_updated': datetime.now().strftime('%Y-%m-%d'),
        }

        if verbose:
            print(f"\n📊 WHALE SUMMARY - {ticker}:")
            print(f"   Buying: {len(buyers)} whales")
            print(f"   Selling: {len(sellers)} whales")
            print(f"   Holding: {len(holders)} whales")
            print(f"   Net Flow: {net_flow}")
            print(f"   Confidence: {confidence}\n")

        return result

    def _get_position_change(self, cik: str, ticker: str, verbose: bool = False) -> Optional[Dict]:
        """
        Get position change for whale in stock

        Compares current quarter vs previous quarter.
        """

        try:
            # Get whale filings (cache)
            cache_key = f"{cik}_filings"
            if cache_key not in self.filing_cache:
                company = Company(cik)
                filings = company.get_filings(form="13F-HR").latest(2)
                if not filings or len(filings) < 2:
                    return None
                self.filing_cache[cache_key] = filings
            else:
                filings = self.filing_cache[cache_key]

            # Use get_filing_at() — filings[i] indexing fails on EntityFilings
            current_filing = filings.get_filing_at(0)
            previous_filing = filings.get_filing_at(1)

            # Staleness guard: reject if the most recent filing is older than 18 months.
            # Some CIKs map to the wrong entity and have stale historical data that
            # would otherwise produce misleading Q-over-Q comparisons.
            from datetime import date
            period_str = str(current_filing.period_of_report or "")[:10]
            if period_str:
                filing_date = date.fromisoformat(period_str)
                cutoff = date(date.today().year - 1, date.today().month, 1)
                if filing_date < cutoff:
                    return None   # stale data — skip this whale

            current_holdings = self._parse_holdings(current_filing)
            previous_holdings = self._parse_holdings(previous_filing)

            current_pos = self._find_ticker(current_holdings, ticker)
            previous_pos = self._find_ticker(previous_holdings, ticker)

            # Calculate change
            if current_pos and previous_pos:
                shares_change = current_pos['shares'] - previous_pos['shares']

                if previous_pos['shares'] > 0:
                    change_pct = (shares_change / previous_pos['shares']) * 100
                else:
                    change_pct = 0

                if abs(shares_change) < 1000:
                    change_type = 'HOLDING'
                elif shares_change > 0:
                    change_type = 'INCREASED'
                else:
                    change_type = 'DECREASED'

                return {
                    'change_type': change_type,
                    'shares_change': shares_change,
                    'change_pct': change_pct,
                    'current_shares': current_pos['shares'],
                    'previous_shares': previous_pos['shares'],
                }

            if current_pos and not previous_pos:
                return {
                    'change_type': 'NEW',
                    'shares_change': current_pos['shares'],
                    'change_pct': 100.0,
                    'current_shares': current_pos['shares'],
                    'previous_shares': 0,
                }

            if previous_pos and not current_pos:
                return {
                    'change_type': 'SOLD',
                    'shares_change': -previous_pos['shares'],
                    'change_pct': -100.0,
                    'current_shares': 0,
                    'previous_shares': previous_pos['shares'],
                }

            return None

        except Exception as e:
            if verbose:
                print(f"   Error processing CIK {cik}: {e}")
            return None

    def _parse_holdings(self, filing: Any) -> List[Dict]:
        """
        Parse 13F filing to get holdings.

        edgartools returns a ThirteenF object from filing.obj() — the actual
        holdings DataFrame is at ThirteenF.holdings. Column names in the
        current edgartools API are: Issuer, Ticker, SharesPrnAmount, Value.
        """

        try:
            thirteenf = filing.obj()
            # .holdings is the DataFrame (not the ThirteenF object itself)
            holdings_df = thirteenf.holdings
            if not isinstance(holdings_df, pd.DataFrame) or holdings_df.empty:
                return []

            holdings = []
            for _, row in holdings_df.iterrows():
                try:
                    holdings.append(
                        {
                            'name':   str(row.get('Issuer', '') or '').upper(),
                            'ticker': str(row.get('Ticker', '') or '').upper(),
                            'shares': int(row.get('SharesPrnAmount', 0) or 0),
                            'value':  int(row.get('Value', 0) or 0),
                        }
                    )
                except Exception:
                    continue

            return holdings

        except Exception:
            return []

    def _find_ticker(self, holdings: List[Dict], ticker: str) -> Optional[Dict]:
        """
        Find ticker in holdings.

        Primary: direct Ticker column match (edgartools populates this ~99%).
        Fallback: issuer name match via TICKER_TO_NAME for any rows missing a ticker.
        """
        ticker_upper = ticker.upper()

        # Direct ticker match first (fast and accurate)
        for holding in holdings:
            if holding.get('ticker') == ticker_upper:
                return holding

        # Name-based fallback for rows where Ticker column was empty
        search_name = self.TICKER_TO_NAME.get(ticker_upper, ticker_upper)
        for holding in holdings:
            if not holding.get('ticker') and search_name in holding.get('name', ''):
                return holding

        return None


# Test
if __name__ == "__main__":
    print("=" * 70)
    print("🐋 WHALE TRACKER 13F - TESTING")
    print("=" * 70)

    if not EDGARTOOLS_AVAILABLE:
        print("\n❌ edgartools not installed")
        print("\nInstall:")
        print("   pip install edgartools pandas --break-system-packages")
        raise SystemExit(1)

    print()
    tracker = WhaleTracker13F(user_email=DEFAULT_SEC_IDENTITY)

    print("\n" + "=" * 70)
    print("Testing with NVDA...")
    print("=" * 70)

    try:
        result = tracker.get_whale_activity_for_stock("NVDA", verbose=True)

        print("=" * 70)
        print("✅ SUCCESS!")
        print(f"Net Flow: {result['net_flow']}")
        print(f"Confidence: {result['confidence']}")

        if result['top_buyers']:
            print("\nTop Buyers:")
            for buyer in result['top_buyers']:
                print(f"  - {buyer['name']}: +{buyer['shares_added']:,}")

    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
