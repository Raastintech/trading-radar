"""
core/whale_tracker.py — 13F institutional activity tracker for VOYAGER.

Data source: SEC EDGAR 13F-HR filings via edgartools (open-source library).
Requires: pip install edgartools

Purpose:
  Provide a slow-moving, quarterly institutional confirmation layer for the
  VOYAGER scanner. 13F filings are delayed (45-day lag after quarter end) and
  do NOT replace live proxies (dollar volume trend, RS, up/down volume ratio).
  This module is a soft enrichment layer only.

Usage:
  from core.whale_tracker import get_whale_tracker
  activity = get_whale_tracker().get_institutional_activity("NVDA")
  # Returns dict with net_flow, confidence, whales_buying, etc.
  # Returns None gracefully if edgartools unavailable or SEC unreachable.

Caching:
  Each ticker lookup is cached for 24 hours via the Gatekeeper.
  13F data only changes quarterly; 24h cache is conservative and appropriate.

Anti-crash policy:
  Every public method is wrapped in try/except. A missing or unavailable 13F
  result returns None — it never blocks the VOYAGER scanner.

Platform identity note:
  This module helps VOYAGER move WITH institutions by confirming that the
  names showing accumulation in our live proxies (dollar volume trend, RS)
  are also seeing real institutional position-building in quarterly filings.
  It is a confirmation layer, not a primary signal generator.
"""
from __future__ import annotations
import logging
import time
from datetime import date
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── edgartools availability guard ────────────────────────────────────────────

try:
    from edgar import Company, set_identity
    _EDGAR_AVAILABLE = True
except ImportError:
    _EDGAR_AVAILABLE = False
    logger.info("edgartools not installed — 13F tracker disabled; install with: pip install edgartools")

# Cache TTL: 24 hours. 13F data changes quarterly; daily refresh is more than sufficient.
_TTL_THIRTEEN_F = 24 * 3600
# Short TTL for UNKNOWN results (institutions_checked=0). Prevents the
# thundering-herd against SEC EDGAR when transient connectivity / rate-limit
# issues cause an entire 16-institution sweep to fail. 10 minutes is long
# enough to dampen back-to-back radar runs, short enough that the next
# scheduled invocation gets a fresh attempt.
_TTL_THIRTEEN_F_UNKNOWN = 10 * 60

# SEC identity required per EDGAR fair-access policy
_SEC_IDENTITY = "hedayat.raastin@gmail.com"

# Staleness guard: reject filings older than 18 months.
# Prevents stale/wrong CIK mappings from producing misleading Q-over-Q data.
_MAX_FILING_AGE_MONTHS = 18


class WhaleTracker:
    """
    Tracks Q-over-Q institutional position changes for a set of major institutions.
    All results are cached 24h. All errors are handled gracefully (return None).

    Tracked institutions (16 verified with fresh 2026-Q1 filings as of 2026-05-13):
      Active (CIK verified):
        Vanguard, BlackRock, State Street, Fidelity, Berkshire Hathaway,
        ARK Investment, Renaissance Technologies, Citadel, DE Shaw, Bridgewater,
        Tiger Global, Point72, Viking Global, Third Point, Lone Pine,
        Soros Fund Management

      Excluded / CIK unresolved:
        Two Sigma, Coatue Management, D1 Capital — no usable 13F CIK found.
        Millennium Management — CIK found but returns no filings via edgartools.

      BlackRock note: prior CIK 0001086364 ("BLACKROCK ADVISORS LLC", last
        filing 2016) and 0001364742 ("BlackRock Finance Inc", through 2024)
        were both stale.  The active parent filer is "BlackRock, Inc." at
        CIK 0002012383 (verified 2026-05-13 — 7 active 13F-HR filings,
        most recent period 2026-03-31 filed 2026-05-13, 5,610 holdings).
    """

    # Verified active institutions (CIK → entity name confirmed via edgartools 2026-04-18;
    # BlackRock CIK refreshed 2026-05-13 to the active parent filer).
    TRACKED_INSTITUTIONS: Dict[str, str] = {
        "Vanguard":              "0000102909",  # VANGUARD GROUP INC
        "BlackRock":             "0002012383",  # BlackRock, Inc. (parent; replaces stale 0001086364)
        "State Street":          "0000093751",  # STATE STREET CORP
        "Fidelity":              "0000315066",  # FMR LLC
        "Berkshire Hathaway":    "0001067983",  # BERKSHIRE HATHAWAY INC
        "ARK Investment":        "0001697748",  # ARK INVESTMENT MANAGEMENT LLC
        "Renaissance Tech":      "0001037389",  # RENAISSANCE TECHNOLOGIES LLC
        "Citadel":               "0001423053",  # CITADEL ADVISORS LLC
        "DE Shaw":               "0001009207",  # D. E. SHAW & CO., INC.
        "Bridgewater":           "0001350694",  # BRIDGEWATER ASSOCIATES, LP
        "Tiger Global":          "0001167483",  # TIGER GLOBAL MANAGEMENT LLC
        "Point72":               "0001603466",  # POINT72 ASSET MANAGEMENT, L.P.
        "Viking Global":         "0001103804",  # VIKING GLOBAL INVESTORS LP
        "Third Point":           "0001040273",  # THIRD POINT LLC
        "Lone Pine":             "0001061165",  # LONE PINE CAPITAL LLC
        "Soros Fund Management": "0001029160",  # SOROS FUND MANAGEMENT LLC
        # Not yet wired — CIKs not resolved:
        # "Two Sigma":    TBD
        # "Millennium":   TBD
        # "Coatue":       TBD
        # "D1 Capital":   TBD
    }

    def __init__(self):
        if not _EDGAR_AVAILABLE:
            raise ImportError("edgartools required: pip install edgartools")
        set_identity(_SEC_IDENTITY)
        self._filing_cache: Dict[str, Any] = {}   # in-memory: {cik: (filings_obj, timestamp)}
        self._gate = None   # lazy-init Gatekeeper to avoid import-time DB open

    # ── Public API ────────────────────────────────────────────────────────────

    def get_institutional_activity(self, ticker: str) -> Optional[Dict]:
        """
        Main entry point. Returns institutional activity summary for a ticker.

        Returns dict with keys:
          ticker, net_flow, confidence, whales_buying, whales_selling,
          whales_holding, total_tracked, top_buyers, top_sellers,
          last_quarter, institutions_checked

        Returns None if edgartools unavailable, SEC unreachable, or any error.
        Always safe to call — never raises.
        """
        if not _EDGAR_AVAILABLE:
            return None
        try:
            return self._get_activity(ticker.upper())
        except Exception as exc:
            logger.debug("13F activity failed for %s: %s", ticker, exc)
            return None

    # ── Internal ──────────────────────────────────────────────────────────────

    def _get_activity(self, ticker: str) -> Optional[Dict]:
        """Core logic with Gatekeeper caching."""
        # Check persistent cache first
        cache_key = f"13f:v2:{ticker}"
        gate = self._get_gate()
        if gate is not None:
            cached = gate.get(cache_key, _TTL_THIRTEEN_F)
            if cached is not None:
                # Fix #2 read side: UNKNOWN entries are cached for 10 min
                # only.  If the cache hit is an UNKNOWN that's older than
                # the short TTL, treat it as a miss and re-sweep.
                if cached.get("net_flow") == "UNKNOWN":
                    cached_at = float(cached.get("cached_at") or 0.0)
                    if (time.time() - cached_at) > _TTL_THIRTEEN_F_UNKNOWN:
                        cached = None
                if cached is not None:
                    return cached

        buyers, sellers, holders = [], [], []
        last_quarter: Optional[str] = None
        institutions_checked = 0

        for institution_name, cik in self.TRACKED_INSTITUTIONS.items():
            try:
                change = self._get_position_change(cik, ticker)
                if change is None:
                    continue
                institutions_checked += 1

                # Track most recent quarter seen
                if change.get("period") and (last_quarter is None or change["period"] > last_quarter):
                    last_quarter = change["period"]

                ct = change["change_type"]
                if ct == "INCREASED":
                    buyers.append({
                        "name":        institution_name,
                        "shares_added": change["shares_change"],
                        "change_pct":   change["change_pct"],
                    })
                elif ct == "DECREASED":
                    sellers.append({
                        "name":           institution_name,
                        "shares_removed": abs(change["shares_change"]),
                        "change_pct":     abs(change["change_pct"]),
                    })
                elif ct in ("HOLDING", "NEW"):
                    holders.append(institution_name)

            except Exception as exc:
                logger.debug("13F position_change failed %s / %s: %s", institution_name, ticker, exc)

        if institutions_checked == 0:
            result = {
                "ticker": ticker, "net_flow": "UNKNOWN", "confidence": "UNKNOWN",
                "whales_buying": 0, "whales_selling": 0, "whales_holding": 0,
                "total_tracked": 0, "top_buyers": [], "top_sellers": [],
                "last_quarter": None, "institutions_checked": 0,
                "cached_at": time.time(),
            }
            # Fix #1: surface the silent failure so it appears in journal /
            # logs (previously logger.debug, invisible at INFO level).  Hit
            # rate of 0/16 institutions almost always means SEC EDGAR
            # rate-limited or connectivity glitched mid-sweep — worth
            # knowing about.
            logger.warning(
                "13F sweep failed for %s: 0/%d institutions returned data — "
                "likely SEC EDGAR transient failure.  Caching UNKNOWN for %ds.",
                ticker, len(self.TRACKED_INSTITUTIONS), _TTL_THIRTEEN_F_UNKNOWN,
            )
            # Fix #2: cache UNKNOWN with a short TTL (10 min) instead of
            # the previous "don't cache" policy.  Stops back-to-back radar
            # invocations from re-hammering SEC during an outage; next run
            # after the short TTL gets a clean retry.  Freshness is
            # enforced on the READ side via the cached_at stamp.
            if gate is not None:
                gate.put(cache_key, result)
            return result

        # Determine net flow
        n_buy, n_sell = len(buyers), len(sellers)
        if n_buy > n_sell and n_buy > 0:
            net_flow = "BUYING"
        elif n_sell > n_buy and n_sell > 0:
            net_flow = "SELLING"
        elif n_buy > 0 or n_sell > 0:
            net_flow = "MIXED"
        else:
            net_flow = "NEUTRAL"

        # Confidence based on breadth
        total_active = n_buy + n_sell + len(holders)
        if total_active >= 5:
            confidence = "HIGH"
        elif total_active >= 3:
            confidence = "MODERATE"
        elif total_active >= 1:
            confidence = "LOW"
        else:
            confidence = "UNKNOWN"

        buyers.sort(key=lambda x: x["shares_added"], reverse=True)
        sellers.sort(key=lambda x: x["shares_removed"], reverse=True)

        result = {
            "ticker":               ticker,
            "net_flow":             net_flow,
            "confidence":           confidence,
            "whales_buying":        n_buy,
            "whales_selling":       n_sell,
            "whales_holding":       len(holders),
            "total_tracked":        total_active,
            "top_buyers":           buyers[:3],
            "top_sellers":          sellers[:3],
            "last_quarter":         last_quarter,
            "institutions_checked": institutions_checked,
        }

        if gate is not None:
            gate.put(cache_key, result)
        return result

    def _get_position_change(self, cik: str, ticker: str) -> Optional[Dict]:
        """
        Compare current vs prior quarter position for one institution / ticker.
        Returns None if institution doesn't hold the name or data unavailable.
        """
        # Use in-memory cache for filings objects (avoids re-fetching per ticker)
        cache_key_filings = f"filings:{cik}"
        now = time.time()

        if cache_key_filings in self._filing_cache:
            cached_filings, cached_at = self._filing_cache[cache_key_filings]
            # Refresh filing objects every 6 hours
            if now - cached_at > 6 * 3600:
                del self._filing_cache[cache_key_filings]

        if cache_key_filings not in self._filing_cache:
            company = Company(cik)
            filings = company.get_filings(form="13F-HR").latest(2)
            if filings is None or len(filings) < 2:
                return None
            self._filing_cache[cache_key_filings] = (filings, now)

        filings, _ = self._filing_cache[cache_key_filings]
        current_filing  = filings.get_filing_at(0)
        previous_filing = filings.get_filing_at(1)

        # Staleness guard: reject if most recent filing is older than 18 months
        period_str = str(current_filing.period_of_report or "")[:10]
        if period_str:
            try:
                filing_date = date.fromisoformat(period_str)
                cutoff_year  = date.today().year - 1
                cutoff_month = date.today().month
                cutoff = date(cutoff_year if cutoff_month > 6 else cutoff_year - 1,
                              max(1, cutoff_month - 6), 1)
                if filing_date < cutoff:
                    return None   # stale — skip this institution
            except ValueError:
                pass

        current_holdings  = self._parse_holdings(current_filing)
        previous_holdings = self._parse_holdings(previous_filing)

        current_pos  = self._find_ticker(current_holdings, ticker)
        previous_pos = self._find_ticker(previous_holdings, ticker)

        if current_pos and previous_pos:
            shares_change = current_pos["shares"] - previous_pos["shares"]
            change_pct = (shares_change / previous_pos["shares"] * 100
                          if previous_pos["shares"] > 0 else 0.0)
            if abs(shares_change) < 1000:
                change_type = "HOLDING"
            elif shares_change > 0:
                change_type = "INCREASED"
            else:
                change_type = "DECREASED"
            return {
                "change_type":    change_type,
                "shares_change":  shares_change,
                "change_pct":     round(change_pct, 1),
                "current_shares": current_pos["shares"],
                "period":         period_str,
            }

        if current_pos and not previous_pos:
            return {
                "change_type":    "NEW",
                "shares_change":  current_pos["shares"],
                "change_pct":     100.0,
                "current_shares": current_pos["shares"],
                "period":         period_str,
            }

        if previous_pos and not current_pos:
            return {
                "change_type":    "SOLD",
                "shares_change":  -previous_pos["shares"],
                "change_pct":     -100.0,
                "current_shares": 0,
                "period":         period_str,
            }

        return None   # institution doesn't hold this ticker

    def _parse_holdings(self, filing: Any) -> List[Dict]:
        """
        Parse 13F filing to holdings list.
        edgartools filing.obj() returns a ThirteenF object; .holdings is the DataFrame.
        Columns: Issuer, Ticker, SharesPrnAmount, Value.
        """
        try:
            thirteenf = filing.obj()
            holdings_df = thirteenf.holdings
            import pandas as pd
            if not isinstance(holdings_df, pd.DataFrame) or holdings_df.empty:
                return []
            result = []
            for _, row in holdings_df.iterrows():
                try:
                    result.append({
                        "name":   str(row.get("Issuer", "") or "").upper(),
                        "ticker": str(row.get("Ticker", "") or "").upper(),
                        "shares": int(row.get("SharesPrnAmount", 0) or 0),
                        "value":  int(row.get("Value", 0) or 0),
                    })
                except Exception:
                    continue
            return result
        except Exception:
            return []

    def _find_ticker(self, holdings: List[Dict], ticker: str) -> Optional[Dict]:
        """
        Locate a ticker in a holdings list.
        Primary: exact Ticker column match (edgartools ~99% populated).
        Fallback: partial issuer name match for rows with empty Ticker.
        """
        ticker_upper = ticker.upper()
        for h in holdings:
            if h.get("ticker") == ticker_upper:
                return h
        # Name-based fallback for any rows missing a ticker symbol
        for h in holdings:
            if not h.get("ticker") and ticker_upper in h.get("name", ""):
                return h
        return None

    def _get_gate(self):
        """Lazy-init Gatekeeper — avoids import-time DB connection."""
        if self._gate is None:
            try:
                from core.data_gatekeeper import get_gatekeeper
                self._gate = get_gatekeeper()
            except Exception:
                self._gate = False   # sentinel: don't retry
        return self._gate if self._gate is not False else None


# ── Module-level singleton ────────────────────────────────────────────────────

_tracker: Optional[WhaleTracker] = None


def get_whale_tracker() -> Optional[WhaleTracker]:
    """
    Returns the module-level WhaleTracker singleton, or None if edgartools
    is not installed. Safe to call in any context — never raises.
    """
    global _tracker
    if _tracker is None:
        if not _EDGAR_AVAILABLE:
            return None
        try:
            _tracker = WhaleTracker()
        except Exception as exc:
            logger.warning("WhaleTracker init failed: %s", exc)
            return None
    return _tracker


def score_thirteen_f(activity: Optional[Dict]) -> int:
    """
    Translate institutional activity dict into a VOYAGER score adjustment.

    Scoring philosophy:
      - 13F is delayed (45-day lag). It must NOT dominate current-quarter signals.
      - Max bonus: +8. Max penalty: -5. Total range: -5 to +8.
      - BUYING + HIGH: strong multi-institution confirmation of accumulation thesis.
      - SELLING + HIGH: meaningful headwind, but not a hard veto (institutions may
        have already exited and the stock is now building a new base).
      - UNKNOWN / unavailable: 0 — no penalty for missing data.

    Scoring uses buy-minus-sell *margin* rather than raw buyer count, so passive
    index funds (Vanguard, State Street, Fidelity) that mechanically accumulate
    every S&P 500 constituent on both sides of the ledger do not inflate scores.
    A ticker where 7 institutions are buying and 6 are selling (margin=1) is
    treated as marginal, not a strong BUYING signal.

    Range:
      BUYING  margin ≥ 4 + HIGH confidence: +8  (strong multi-fund conviction)
      BUYING  margin ≥ 3:                   +5
      BUYING  margin ≥ 2:                   +3
      BUYING  margin = 1:                   +1  (marginal — index inflow noise)
      MIXED   (buy ≥ sell):                 +1
      MIXED   (sell > buy):                  0
      NEUTRAL / UNKNOWN / unavailable:       0
      SELLING margin = 1:                   -2
      SELLING margin ≥ 2:                   -3
      SELLING margin ≥ 3 + HIGH confidence: -5  (broad institutional exit)
    """
    if activity is None:
        return 0

    net_flow   = activity.get("net_flow",   "UNKNOWN")
    confidence = activity.get("confidence", "UNKNOWN")
    n_buy      = activity.get("whales_buying",  0)
    n_sell     = activity.get("whales_selling", 0)

    if net_flow == "BUYING":
        margin = n_buy - n_sell
        if margin >= 4 and confidence == "HIGH": return  8
        if margin >= 3:                          return  5
        if margin >= 2:                          return  3
        return  1   # margin == 1: marginal buying — index inflow noise

    if net_flow == "SELLING":
        margin = n_sell - n_buy
        if margin >= 3 and confidence == "HIGH": return -5
        if margin >= 2:                          return -3
        return -2   # margin == 1: marginal selling

    if net_flow == "MIXED":
        return 1 if n_buy >= n_sell else 0

    return 0   # NEUTRAL, UNKNOWN, or unavailable
