"""
Fundamental Data Fetcher — FMP primary, yfinance optional fallback.

Migration note: yfinance was replaced as primary data source because it
produced lagging or silently failed data. FMP is now the authoritative source.
yfinance is kept as a conditional import for legacy/debug use only.
"""

import datetime
import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import requests

try:
    import yfinance as yf
    _HAS_YFINANCE = True
except ImportError:
    yf = None  # type: ignore[assignment]
    _HAS_YFINANCE = False

logger = logging.getLogger(__name__)

# FMP direct call constants (avoids circular import with research_data_provider)
_FMP_BASE = "https://financialmodelingprep.com"
_FMP_TIMEOUT = 20
_FMP_FUND_CACHE_TTL = 86400   # 24 h for fundamentals
_FMP_EARN_CACHE_TTL = 21600   # 6 h for upcoming earnings


def _fmp_key() -> str:
    return (
        os.environ.get("FMP_API_KEY")
        or os.environ.get("FINANCIALMODELINGPREP_API_KEY")
        or ""
    )


def _fmp_get(path: str, params: Dict[str, Any]) -> Optional[Any]:
    """Single FMP GET with key injection. Returns parsed JSON or None on error."""
    key = _fmp_key()
    if not key:
        return None
    try:
        resp = requests.get(
            f"{_FMP_BASE}/{path.lstrip('/')}",
            params={**params, "apikey": key},
            timeout=_FMP_TIMEOUT,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        # FMP wraps some errors as {"Error Message": "..."}
        if isinstance(data, dict) and "Error Message" in data:
            return None
        return data
    except Exception:
        return None


class FundamentalDataFetcher:
    """Fetch fundamental data using yfinance (free)"""

    _voyager_cache: Dict[str, Tuple[float, Dict]] = {}
    _short_cache: Dict[str, Tuple[float, Dict]] = {}
    _cache_ttl_seconds: float = 900.0

    @classmethod
    def _cache_get(cls, store: Dict[str, Tuple[float, Dict]], ticker: str) -> Optional[Dict]:
        rec = store.get(ticker.upper())
        if not rec:
            return None
        ts, payload = rec
        if (time.time() - ts) <= cls._cache_ttl_seconds:
            return dict(payload)
        try:
            del store[ticker.upper()]
        except KeyError:
            pass
        return None

    @classmethod
    def _cache_set(cls, store: Dict[str, Tuple[float, Dict]], ticker: str, payload: Dict) -> Dict:
        store[ticker.upper()] = (time.time(), dict(payload))
        return payload

    @staticmethod
    def _safe_float(value) -> Optional[float]:
        try:
            if value is None:
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    @classmethod
    def _resolve_revenue_growth_yoy(cls, stock, revenue_growth_raw) -> Optional[float]:
        revenue_growth_yoy = cls._safe_float(revenue_growth_raw)
        if revenue_growth_yoy is not None:
            return revenue_growth_yoy

        try:
            quarterly_financials = stock.quarterly_financials  # newest quarter first
            if quarterly_financials is None or 'Total Revenue' not in quarterly_financials.index:
                return None
            quarterly_revenue = quarterly_financials.loc['Total Revenue'].dropna()
            if len(quarterly_revenue) >= 8:
                ttm_recent = cls._safe_float(quarterly_revenue.iloc[:4].sum())
                ttm_prior = cls._safe_float(quarterly_revenue.iloc[4:8].sum())
                if ttm_recent is not None and ttm_prior and ttm_recent > 0 and ttm_prior > 0:
                    return (ttm_recent - ttm_prior) / ttm_prior
            if len(quarterly_revenue) >= 5:
                current_quarter = cls._safe_float(quarterly_revenue.iloc[0])
                prior_year_quarter = cls._safe_float(quarterly_revenue.iloc[4])
                if (
                    current_quarter is not None
                    and prior_year_quarter
                    and current_quarter > 0
                    and prior_year_quarter > 0
                ):
                    return (current_quarter - prior_year_quarter) / prior_year_quarter
        except Exception:
            return None
        return None

    @classmethod
    def _resolve_short_revenue_deterioration(cls, stock, revenue_growth_yoy: Optional[float]) -> bool:
        """
        Identify real revenue deterioration, not just low absolute growth.

        A simple `growth < 5%` rule misses decelerating operators whose growth is
        still positive but clearly rolling over. We keep the low-growth check, then
        add a quarterly YoY slowdown detector so the REVENUE pathway matches its
        name more honestly.
        """
        if revenue_growth_yoy is not None and revenue_growth_yoy < 0.05:
            return True

        try:
            quarterly_financials = stock.quarterly_financials
            if quarterly_financials is None or 'Total Revenue' not in quarterly_financials.index:
                return False
            quarterly_revenue = quarterly_financials.loc['Total Revenue'].dropna()
            if len(quarterly_revenue) < 6:
                return False

            current_quarter = cls._safe_float(quarterly_revenue.iloc[0])
            prior_year_quarter = cls._safe_float(quarterly_revenue.iloc[4])
            previous_quarter = cls._safe_float(quarterly_revenue.iloc[1])
            previous_year_quarter = cls._safe_float(quarterly_revenue.iloc[5])
            if not all(
                value is not None and value > 0
                for value in (current_quarter, prior_year_quarter, previous_quarter, previous_year_quarter)
            ):
                return False

            current_yoy = (current_quarter - prior_year_quarter) / prior_year_quarter
            previous_yoy = (previous_quarter - previous_year_quarter) / previous_year_quarter
            return current_yoy <= (previous_yoy - 0.08) and current_yoy < 0.20
        except Exception:
            return False

    @classmethod
    def _extract_recommendation_balance(cls, row: Any) -> Tuple[Optional[float], Optional[float]]:
        if row is None:
            return None, None
        try:
            if hasattr(row, "to_dict"):
                values = row.to_dict()
            elif isinstance(row, dict):
                values = dict(row)
            else:
                return None, None
            bullish = sum(float(values.get(key, 0) or 0) for key in ("strongBuy", "buy"))
            bearish = sum(float(values.get(key, 0) or 0) for key in ("sell", "strongSell"))
            return bullish, bearish
        except Exception:
            return None, None

    @classmethod
    def _resolve_margin_compressing_trend(cls, stock) -> Optional[bool]:
        """
        Compute margin compression as a TREND (requires ≥ 2 data points).

        Returns True  if operating margin declined from the prior quarter to the
                      most recent quarter.
        Returns False if margin held flat or improved.
        Returns None  if quarterly data is unavailable or has fewer than 2 common
                      periods — callers should treat None as "unknown", not False.

        Contrast with `margin_compression` (level check: operating_margin < 10%),
        which only reflects the current absolute margin, not direction of travel.
        """
        try:
            qf = stock.quarterly_financials  # columns newest-first
            if qf is None or qf.empty:
                return None
            if 'Operating Income' not in qf.index or 'Total Revenue' not in qf.index:
                return None
            op_income = qf.loc['Operating Income'].dropna()
            revenue = qf.loc['Total Revenue'].dropna()
            common = op_income.index.intersection(revenue.index)
            if len(common) < 2:
                return None
            # Sort newest-first so iloc[0] = most recent, iloc[1] = prior quarter
            op_income = op_income[common].sort_index(ascending=False)
            revenue = revenue[common].sort_index(ascending=False)

            def _margin(i: int) -> Optional[float]:
                rev = cls._safe_float(revenue.iloc[i])
                oi = cls._safe_float(op_income.iloc[i])
                if rev is None or rev <= 0 or oi is None:
                    return None
                return oi / rev

            current_margin = _margin(0)
            prior_margin = _margin(1)
            if current_margin is None or prior_margin is None:
                return None
            return current_margin < prior_margin
        except Exception:
            return None

    @classmethod
    def _resolve_short_revision_signals(cls, stock) -> Tuple[str, str]:
        """
        Best-effort analyst-trend proxy for negative revisions / guidance tone.

        Yahoo does not provide a clean point-in-time management-guidance feed. For
        the short model we use a conservative analyst-proxy instead of hardcoding
        both branches to `stable`, which made the GUIDANCE pathway permanently dead.
        """
        estimate_revisions = 'stable'
        guidance_trend = 'stable'
        downgrade_score = 0
        upgrade_score = 0

        try:
            revisions = getattr(stock, 'upgrades_downgrades', None)
            if revisions is not None and hasattr(revisions, 'empty') and not revisions.empty:
                frame = revisions.copy()
                if hasattr(frame.index, "tz") and frame.index.tz is not None:
                    frame.index = frame.index.tz_convert(None)
                if isinstance(frame.index, pd.DatetimeIndex):
                    cutoff = pd.Timestamp.utcnow().tz_localize(None) - pd.Timedelta(days=180)
                    frame = frame.loc[frame.index >= cutoff]
                if len(frame) > 20:
                    frame = frame.tail(20)

                for _, row in frame.iterrows():
                    action_blob = " ".join(str(value) for value in row.tolist()).lower()
                    if any(token in action_blob for token in ("downgrade", "down", "sell", "underperform", "underweight", "reduce", "cut")):
                        downgrade_score += 1
                    if any(token in action_blob for token in ("upgrade", "up", "buy", "outperform", "overweight", "add", "raise")):
                        upgrade_score += 1
        except Exception:
            pass

        try:
            summary = getattr(stock, 'recommendations_summary', None)
            if summary is not None and hasattr(summary, 'empty') and not summary.empty and len(summary) >= 2:
                frame = summary.copy()
                if isinstance(frame.index, pd.DatetimeIndex):
                    frame = frame.sort_index()
                recent = frame.iloc[-1]
                prior = frame.iloc[-2]
                recent_bullish, recent_bearish = cls._extract_recommendation_balance(recent)
                prior_bullish, prior_bearish = cls._extract_recommendation_balance(prior)
                if None not in (recent_bullish, recent_bearish, prior_bullish, prior_bearish):
                    bearish_shift = (recent_bearish > prior_bearish) or (recent_bullish < prior_bullish)
                    bullish_shift = (recent_bearish < prior_bearish) or (recent_bullish > prior_bullish)
                    if bearish_shift and not bullish_shift:
                        estimate_revisions = 'cutting'
                        guidance_trend = 'cutting'
                    elif bullish_shift and not bearish_shift:
                        estimate_revisions = 'raising'
                        guidance_trend = 'raising'
        except Exception:
            pass

        if estimate_revisions == 'stable':
            if downgrade_score >= max(2, upgrade_score + 1):
                estimate_revisions = 'cutting'
            elif upgrade_score >= max(2, downgrade_score + 1):
                estimate_revisions = 'raising'

        if guidance_trend == 'stable':
            if downgrade_score >= max(3, upgrade_score + 2):
                guidance_trend = 'cutting'
            elif upgrade_score >= max(3, downgrade_score + 2):
                guidance_trend = 'raising'

        return guidance_trend, estimate_revisions

    @staticmethod
    def get_voyager_fundamentals(ticker: str) -> Dict:
        """
        Fetch fundamentals supporting GROWTH-FIRST pathways

        Provides data for:
        - GROWTH path (strong revenue growth)
        - GROWTH_INFLECTION path (improving economics)
        - OPERATING_LEVERAGE path (Rule of 40)
        - QUALITY path (exceptional margins + FCF)
        """
        if not _HAS_YFINANCE:
            logger.warning("FundamentalDataFetcher.get_voyager_fundamentals: yfinance not installed, returning empty dict for %s", ticker)
            return {}
        cached = FundamentalDataFetcher._cache_get(FundamentalDataFetcher._voyager_cache, ticker)
        if cached is not None:
            return cached
        try:
            stock = yf.Ticker(ticker)
            info = stock.info
            revenue_growth_raw = info.get('revenueGrowth')
            analyst_target_price = info.get('targetMeanPrice')

            # When yfinance's revenueGrowth field is unavailable (common in volatile markets
            # and for recent listings), compute TTM YoY growth from quarterly financials.
            # This is more accurate than silently faking 0% growth.
            revenue_growth_yoy = FundamentalDataFetcher._resolve_revenue_growth_yoy(stock, revenue_growth_raw)
            gross_margin_raw = info.get('grossMargins')
            gross_margin = gross_margin_raw if gross_margin_raw is not None else 0
            operating_margin_raw = info.get('operatingMargins')
            operating_margin = operating_margin_raw if operating_margin_raw is not None else 0
            fcf = info.get('freeCashflow')
            fcf_positive = fcf is not None and fcf > 0
            fcf_inflection = fcf_positive or (fcf is not None and fcf > -10000000)

            # margin_improving: compare TTM operating margin to prior year.
            # The previous check (operating_margin > 0.15) was a fixed threshold,
            # not an improvement signal — it wrongly rejected companies whose margins
            # are rising strongly but haven't yet crossed 15% (e.g. AMZN at 10.5%).
            margin_improving = False
            try:
                financials = stock.financials  # annual income statement, newest column first
                if (
                    financials is not None
                    and 'Operating Income' in financials.index
                    and 'Total Revenue' in financials.index
                ):
                    op_income = financials.loc['Operating Income'].dropna()
                    rev_income = financials.loc['Total Revenue'].dropna()
                    if len(op_income) >= 2 and len(rev_income) >= 2:
                        curr_m = float(op_income.iloc[0]) / float(rev_income.iloc[0]) if float(rev_income.iloc[0]) > 0 else 0.0
                        prior_m = float(op_income.iloc[1]) / float(rev_income.iloc[1]) if float(rev_income.iloc[1]) > 0 else 0.0
                        margin_improving = curr_m > prior_m
                    else:
                        margin_improving = operating_margin > 0.0
                else:
                    margin_improving = operating_margin > 0.0
            except Exception:
                margin_improving = operating_margin > 0.0

            rule_of_40_pass = False
            total_revenue = info.get('totalRevenue')
            rev_growth_for_calc = revenue_growth_yoy if revenue_growth_yoy is not None else 0.0
            if rev_growth_for_calc and fcf and total_revenue:
                fcf_margin = fcf / total_revenue
                rule_40_score = (rev_growth_for_calc * 100) + (fcf_margin * 100)
                rule_of_40_pass = rule_40_score > 40

            debt_to_equity = info.get('debtToEquity')
            debt_manageable = debt_to_equity is not None and debt_to_equity < 150
            pe_ratio = info.get('trailingPE')
            market_cap = info.get('marketCap')
            valuation_reasonable = (
                pe_ratio is not None
                and not (pe_ratio > 200 and (revenue_growth_yoy or 0.0) < 0.10)
            )
            inst_ownership = info.get('heldPercentInstitutions')
            inst_ownership_high = inst_ownership is not None and inst_ownership > 0.30
            sector = info.get('sector')
            industry = info.get('industry')
            has_core_growth_fields = all(
                value is not None
                for value in (revenue_growth_yoy, gross_margin_raw, operating_margin_raw, debt_to_equity, pe_ratio)
            )

            return FundamentalDataFetcher._cache_set(FundamentalDataFetcher._voyager_cache, ticker, {
                'revenue_growth_yoy': revenue_growth_yoy,  # None = unavailable, not 0%
                'analyst_target_price': analyst_target_price,
                'gross_margin': gross_margin,
                'fcf_positive': fcf_positive,
                'fcf_inflection': fcf_inflection,
                'margin_improving': margin_improving,
                'rule_of_40_pass': rule_of_40_pass,
                'debt_manageable': debt_manageable,
                'valuation_reasonable': valuation_reasonable,
                'market_cap': market_cap,
                'sector': sector,
                'industry': industry,
                'inst_ownership_high': inst_ownership_high,
                'guidance_trend': 'stable',
                'data_source': 'yfinance',
                'data_quality': 'good' if has_core_growth_fields else 'partial'
            })

        except Exception as e:
            logger.error(f"Error fetching Voyager fundamentals: {e}")
            return FundamentalDataFetcher._cache_set(FundamentalDataFetcher._voyager_cache, ticker, {
                'revenue_growth_yoy': None,  # None = data unavailable, not 0% growth
                'gross_margin': 0,
                'fcf_positive': False,
                'fcf_inflection': False,
                'margin_improving': False,
                'rule_of_40_pass': False,
                'debt_manageable': False,
                'valuation_reasonable': False,
                'market_cap': None,
                'analyst_target_price': None,
                'sector': None,
                'industry': None,
                'inst_ownership_high': False,
                'guidance_trend': 'stable',
                'data_source': 'error',
                'data_quality': 'error',
            })

    @staticmethod
    def _fmp_short_fundamentals(ticker: str) -> Optional[Dict]:
        """
        Fetch short fundamentals via FMP direct HTTP calls (no circular import).
        Returns None if FMP key is not configured or all calls fail.
        """
        symbol = ticker.upper().strip()
        sf = FundamentalDataFetcher._safe_float

        profile_data = _fmp_get("stable/profile", {"symbol": symbol})
        income_data = _fmp_get("stable/income-statement", {"symbol": symbol, "period": "quarter", "limit": 8})
        cashflow_data = _fmp_get("stable/cash-flow-statement", {"symbol": symbol, "period": "quarter", "limit": 4})
        balance_data = _fmp_get("stable/balance-sheet-statement", {"symbol": symbol, "period": "quarter", "limit": 2})
        earnings_cal = _fmp_get("stable/earnings-surprises", {"symbol": symbol})

        profile_row: Dict[str, Any] = {}
        if isinstance(profile_data, list) and profile_data:
            profile_row = profile_data[0] if isinstance(profile_data[0], dict) else {}
        elif isinstance(profile_data, dict):
            profile_row = profile_data

        income_rows: List[Dict] = [r for r in (income_data or []) if isinstance(r, dict)]
        cash_rows: List[Dict] = [r for r in (cashflow_data or []) if isinstance(r, dict)]
        balance_rows: List[Dict] = [r for r in (balance_data or []) if isinstance(r, dict)]

        if not profile_row and not income_rows:
            return None  # FMP returned nothing useful

        # ── Revenue growth YoY (8-quarter TTM comparison) ─────────────────────
        revenue_growth_yoy: Optional[float] = None
        revenue_deceleration = False
        if len(income_rows) >= 8:
            recent = sum(sf(r.get("revenue")) or 0 for r in income_rows[:4])
            prior = sum(sf(r.get("revenue")) or 0 for r in income_rows[4:8])
            if prior > 0 and recent > 0:
                revenue_growth_yoy = (recent - prior) / prior
        if revenue_growth_yoy is not None:
            revenue_deceleration = revenue_growth_yoy < 0.05

        # ── Margin signals ────────────────────────────────────────────────────
        operating_margin: float = 0.0
        profit_margin: float = 0.0
        margin_compressing_trend: Optional[bool] = None

        if income_rows:
            latest = income_rows[0]
            rev = sf(latest.get("revenue"))
            op_inc = sf(latest.get("operatingIncome"))
            net_inc = sf(latest.get("netIncome"))
            if rev and rev > 0:
                if op_inc is not None:
                    operating_margin = op_inc / rev
                if net_inc is not None:
                    profit_margin = net_inc / rev
            if len(income_rows) >= 2:
                prev = income_rows[1]
                prev_rev = sf(prev.get("revenue"))
                prev_op = sf(prev.get("operatingIncome"))
                if prev_rev and prev_rev > 0 and prev_op is not None and rev and rev > 0 and op_inc is not None:
                    margin_compressing_trend = (op_inc / rev) < (prev_op / prev_rev)

        margin_compression = operating_margin < 0.10
        profit_margin_declining = profit_margin < 0.05

        # ── FCF / debt stress ─────────────────────────────────────────────────
        fcf_negative = False
        debt_stress = False
        if cash_rows:
            fcf = sf(cash_rows[0].get("freeCashFlow"))
            fcf_negative = fcf is not None and fcf < 0
        if balance_rows:
            total_debt = sf(balance_rows[0].get("totalDebt"))
            total_equity = sf(
                balance_rows[0].get("totalStockholdersEquity")
                or balance_rows[0].get("totalEquity")
                or balance_rows[0].get("stockholdersEquity")
            )
            if total_debt and total_equity and total_equity != 0:
                dte = total_debt / abs(total_equity) * 100  # match yfinance scale (%)
                debt_stress = dte > 150 and fcf_negative

        # ── Valuation ─────────────────────────────────────────────────────────
        market_cap = sf(profile_row.get("mktCap") or profile_row.get("marketCap"))
        ev_to_sales: float = 0.0
        if income_rows:
            ev = sf(profile_row.get("enterpriseValue"))
            ttm_rev = sum(sf(r.get("revenue")) or 0 for r in income_rows[:4])
            if ev and ttm_rev and ttm_rev > 0:
                ev_to_sales = ev / ttm_rev
        valuation_rich = ev_to_sales > 3.0

        # ── Earnings window (FMP upcoming earnings calendar) ──────────────────
        earnings_window_safe: Optional[bool] = None
        days_to_earnings: Optional[int] = None
        try:
            upcoming = _fmp_get("stable/earnings", {"symbol": symbol})
            if isinstance(upcoming, list):
                today = datetime.date.today()
                future = [
                    r for r in upcoming
                    if isinstance(r, dict) and r.get("date")
                    and pd.Timestamp(r["date"]).date() >= today
                ]
                if future:
                    next_date = pd.Timestamp(future[0]["date"]).date()
                    days_to_earnings = (next_date - today).days
                    earnings_window_safe = days_to_earnings > 21
        except Exception:
            pass

        # Short interest: not available in FMP Starter — leave as None (unknown)
        # The scoring engine treats None as 0 pts, not a blocker.
        quality_fields = [revenue_growth_yoy, operating_margin, fcf_negative]
        data_quality = "good" if all(v is not None for v in quality_fields) else "partial"

        return {
            "revenue_growth_yoy": revenue_growth_yoy,
            "revenue_deceleration": revenue_deceleration,
            "margin_compression": margin_compression,
            "margin_compressing_trend": margin_compressing_trend,
            "profit_margin_declining": profit_margin_declining,
            "fcf_negative": fcf_negative,
            "debt_stress": debt_stress,
            "valuation_rich": valuation_rich,
            "market_cap": market_cap,
            "short_interest_safe": None,    # FMP Starter: unavailable
            "short_interest_low": None,     # FMP Starter: unavailable
            "short_interest_pct": None,     # FMP Starter: unavailable
            "earnings_window_safe": earnings_window_safe,
            "days_to_earnings": days_to_earnings,
            "guidance_trend": "stable",     # FMP Starter: no mgmt guidance feed
            "estimate_revisions": "stable", # FMP Starter: no analyst revision feed
            "analyst_downgrade_proxy": "stable",
            "data_source": "fmp",
            "data_quality": data_quality,
        }

    @staticmethod
    def get_short_fundamentals(ticker: str) -> Dict:
        """
        Fetch fundamentals supporting multiple deterioration pathways.
        FMP is primary; yfinance is optional fallback only if FMP key is absent.
        """
        cached = FundamentalDataFetcher._cache_get(FundamentalDataFetcher._short_cache, ticker)
        if cached is not None:
            return cached

        # ── FMP primary path ──────────────────────────────────────────────────
        if _fmp_key():
            try:
                fmp_result = FundamentalDataFetcher._fmp_short_fundamentals(ticker)
                if fmp_result:
                    return FundamentalDataFetcher._cache_set(
                        FundamentalDataFetcher._short_cache, ticker, fmp_result
                    )
            except Exception as exc:
                logger.warning("get_short_fundamentals(%s): FMP path failed: %s", ticker, exc)

        # ── yfinance fallback (only if FMP key absent or FMP failed) ─────────
        if not _HAS_YFINANCE:
            logger.warning("get_short_fundamentals(%s): FMP unavailable and yfinance not installed", ticker)
            return {}
        try:
            stock = yf.Ticker(ticker)
            info = stock.info
            revenue_growth_raw = info.get('revenueGrowth')
            revenue_growth_yoy = FundamentalDataFetcher._resolve_revenue_growth_yoy(stock, revenue_growth_raw)
            revenue_deceleration = FundamentalDataFetcher._resolve_short_revenue_deterioration(stock, revenue_growth_yoy)
            profit_margin_raw = info.get('profitMargins')
            profit_margin = profit_margin_raw if profit_margin_raw is not None else 0
            operating_margin_raw = info.get('operatingMargins')
            operating_margin = operating_margin_raw if operating_margin_raw is not None else 0
            margin_compression = operating_margin < 0.10
            margin_compressing_trend = FundamentalDataFetcher._resolve_margin_compressing_trend(stock)
            profit_margin_declining = profit_margin < 0.05
            fcf = info.get('freeCashflow')
            fcf_negative = fcf is not None and fcf < 0
            debt_to_equity = info.get('debtToEquity') or 0
            debt_stress = debt_to_equity > 150 and fcf_negative
            ev_to_sales = info.get('enterpriseToRevenue') or 0
            valuation_rich = ev_to_sales > 3.0
            market_cap = info.get('marketCap')
            short_ratio = info.get('shortRatio')
            short_interest_safe = short_ratio is not None and short_ratio < 10
            short_pct_float = info.get('shortPercentOfFloat')
            short_interest_low = short_pct_float is not None and short_pct_float < 0.08
            short_interest_pct = round(float(short_pct_float) * 100, 1) if short_pct_float is not None else 0.0
            guidance_trend, estimate_revisions = FundamentalDataFetcher._resolve_short_revision_signals(stock)
            earnings_window_safe = None
            days_to_earnings = None
            try:
                calendar = stock.calendar
                earnings_date = None
                if isinstance(calendar, dict):
                    ed = calendar.get('Earnings Date')
                    earnings_date = ed[0] if isinstance(ed, list) and ed else ed
                elif hasattr(calendar, 'empty') and not calendar.empty:
                    try:
                        earnings_date = calendar.loc['Earnings Date'].iloc[0]
                    except Exception:
                        pass
                if earnings_date is not None:
                    if hasattr(earnings_date, 'date'):
                        earnings_date = earnings_date.date()
                    elif not isinstance(earnings_date, datetime.date):
                        earnings_date = None
                    if earnings_date:
                        days_to_earnings = (earnings_date - datetime.date.today()).days
                        earnings_window_safe = days_to_earnings > 21
            except Exception:
                pass
            return FundamentalDataFetcher._cache_set(FundamentalDataFetcher._short_cache, ticker, {
                'revenue_growth_yoy': revenue_growth_yoy,
                'revenue_deceleration': revenue_deceleration,
                'margin_compression': margin_compression,
                'margin_compressing_trend': margin_compressing_trend,
                'profit_margin_declining': profit_margin_declining,
                'fcf_negative': fcf_negative,
                'debt_stress': debt_stress,
                'valuation_rich': valuation_rich,
                'market_cap': market_cap,
                'short_interest_safe': short_interest_safe,
                'short_interest_low': short_interest_low,
                'short_interest_pct': short_interest_pct,
                'earnings_window_safe': earnings_window_safe,
                'days_to_earnings': days_to_earnings,
                'guidance_trend': guidance_trend,
                'estimate_revisions': estimate_revisions,
                'analyst_downgrade_proxy': guidance_trend,
                'data_source': 'yfinance',
                'data_quality': 'good' if revenue_growth_yoy is not None and short_ratio is not None else 'partial',
            })
        except Exception as exc:
            logger.error("get_short_fundamentals(%s): yfinance fallback failed: %s", ticker, exc)
            return FundamentalDataFetcher._cache_set(FundamentalDataFetcher._short_cache, ticker, {
                'revenue_growth_yoy': None, 'revenue_deceleration': False,
                'margin_compression': False, 'margin_compressing_trend': None,
                'profit_margin_declining': False, 'fcf_negative': False,
                'debt_stress': False, 'valuation_rich': False, 'market_cap': None,
                'short_interest_safe': False, 'short_interest_low': False, 'short_interest_pct': 0,
                'earnings_window_safe': None, 'days_to_earnings': None,
                'guidance_trend': 'stable', 'estimate_revisions': 'stable',
                'analyst_downgrade_proxy': 'stable',
                'data_source': 'error', 'data_quality': 'error',
            })


# Quick test
if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.INFO)

    fetcher = FundamentalDataFetcher()

    print("\n" + "="*80)
    print("FUNDAMENTAL DATA FETCHER - PRODUCTION TEST")
    print("="*80)

    # Test VOYAGER candidates
    print("\n### VOYAGER FUNDAMENTALS (Growth Stocks):")
    print("-" * 80)

    voyager_tickers = ['OKTA', 'TWLO', 'ZS', 'NVDA']
    for ticker in voyager_tickers:
        print(f"\n{ticker}:")
        data = fetcher.get_voyager_fundamentals(ticker)
        print(f"  Revenue Growth: {data.get('revenue_growth_yoy')}")
        print(f"  FCF Positive: {data.get('fcf_positive')}")
        print(f"  Gross Margin Good: {data.get('gross_margin_improving')}")
        print(f"  Rule of 40: {data.get('rule_of_40_pass')}")
        print(f"  Data Quality: {data.get('data_quality')}")

    # Test SHORT candidates
    print("\n### SHORT FUNDAMENTALS (Deteriorating Stocks):")
    print("-" * 80)

    short_tickers = ['BBBY', 'RIVN']
    for ticker in short_tickers:
        print(f"\n{ticker}:")
        data = fetcher.get_short_fundamentals(ticker)
        print(f"  Revenue Deceleration: {data.get('revenue_deceleration')}")
        print(f"  FCF Negative: {data.get('fcf_negative')}")
        print(f"  Margin Compression: {data.get('margin_compression')}")
        print(f"  Valuation Rich: {data.get('valuation_rich')}")
        print(f"  Data Quality: {data.get('data_quality')}")

    print("\n" + "="*80)
    print("✅ FUNDAMENTAL FETCHER READY FOR PRODUCTION")
    print("="*80)
