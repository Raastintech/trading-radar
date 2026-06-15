from alpaca_data import AlpacaDataFeed
from alpaca.trading.client import TradingClient
from config import TradingConfig
import statistics
import sqlite3
import os

class PortfolioManager:
    """
    Manages portfolio-level risk:
    - Sector concentration
    - Position correlation
    - Portfolio heat (total risk exposure)

    Quant upgrades:
    - Fix correlation math + timestamp alignment
    - Use ABS correlation for dependency risk
    - HARD vs SOFT veto tiers + remediation
    """

    def __init__(self):
        self.config = TradingConfig()
        self.data_feed = AlpacaDataFeed()
        self.db_path = "trading_performance.db"

        # Trading client to check positions
        self.trading_client = TradingClient(
            self.config.ALPACA_API_KEY,
            self.config.ALPACA_SECRET_KEY,
            paper=True
        )

        # --- Risk Policy (tune later) ---
        self.SECTOR_HARD_CAP_PCT = 45.0
        self.SECTOR_SOFT_WARN_PCT = 30.0

        self.CORR_HARD_ABS = 0.85
        self.CORR_SOFT_ABS = 0.70

        self.HEAT_HARD_CAP = 15.0
        self.HEAT_SOFT_WARN = 12.0
        try:
            self.UNKNOWN_STOP_FALLBACK_PCT = float(
                os.getenv("PORTFOLIO_UNKNOWN_STOP_FALLBACK_PCT", "0.08") or 0.08
            )
        except Exception:
            self.UNKNOWN_STOP_FALLBACK_PCT = 0.08

        # Sector classifications
        self.sector_map = {
            # Space & Aerospace
            'RKLB': 'Space', 'ASTS': 'Space', 'ASTR': 'Space',
            'SATL': 'Space', 'RDW': 'Space',

            # Nuclear & Energy
            'OKLO': 'Nuclear', 'SMR': 'Nuclear', 'EOSE': 'CleanEnergy',
            'BE': 'CleanEnergy', 'TAC': 'Energy',

            # Crypto & Blockchain
            'COIN': 'Crypto', 'IREN': 'Crypto', 'MARA': 'Crypto',
            'RIOT': 'Crypto', 'APLD': 'Crypto', 'IBIT': 'Crypto',

            # Tech & AI
            'NVDA': 'Tech', 'AMD': 'Tech', 'INTC': 'Tech',
            'MSFT': 'Tech', 'META': 'Tech', 'GOOGL': 'Tech',
            'AMZN': 'Tech', 'AAPL': 'Tech', 'NVTS': 'Tech',
            'BBAI': 'AI', 'POET': 'AI', 'IONQ': 'AI',

            # Real Estate & Finance
            'OPEN': 'RealEstate', 'SOFI': 'Fintech', 'HOOD': 'Fintech',
            'DLO': 'Fintech', 'PLTR': 'Tech',

            # Healthcare & Biotech
            'HIMS': 'Healthcare', 'AEHR': 'Semiconductor',

            # Consumer & Restaurants
            'CAVA': 'Restaurant', 'BROS': 'Restaurant',
            'CMG': 'Restaurant', 'CAKE': 'Restaurant',

            # Leveraged ETFs
            'TQQQ': 'Leveraged', 'SOXL': 'Leveraged', 'MSOS': 'Cannabis'
        }

        print("✅ Portfolio Manager initialized")

    # -----------------------------
    # Core portfolio state
    # -----------------------------
    def get_current_positions(self):
        """Get all open positions"""
        try:
            positions = self.trading_client.get_all_positions()
            return [{
                'symbol': pos.symbol,
                'qty': abs(int(float(pos.qty))),
                'direction': (
                    str(getattr(pos, 'side', '') or '').upper()
                    if getattr(pos, 'side', None)
                    else ('SHORT' if float(pos.qty) < 0 or float(pos.market_value) < 0 else 'LONG')
                ),
                'avg_entry': float(pos.avg_entry_price),
                'current_price': float(pos.current_price),
                'market_value': abs(float(pos.market_value)),
                'unrealized_pl': float(pos.unrealized_pl),
                'unrealized_pl_pct': float(pos.unrealized_plpc) * 100
            } for pos in positions]
        except:
            return []

    def _load_open_trade_risk_map(self):
        """
        Load latest open-trade stop metadata keyed by ticker.

        Uses the canonical trade log first so portfolio heat reflects actual
        planned risk instead of a flat per-position proxy.
        """
        risk_map = {}
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(
                """
                SELECT ticker, strategy, direction, quantity, entry_price, stop_loss, initial_stop_loss
                FROM trades
                WHERE (status = 'OPEN' OR exit_date IS NULL)
                ORDER BY COALESCE(entry_time, entry_date) DESC, id DESC
                """
            )
            for row in cur.fetchall():
                ticker = str(row["ticker"] or "").upper()
                if not ticker or ticker in risk_map:
                    continue
                stop = row["stop_loss"]
                if stop is None:
                    stop = row["initial_stop_loss"]
                risk_map[ticker] = {
                    "strategy": str(row["strategy"] or "").upper() if "strategy" in row.keys() else "",
                    "direction": str(row["direction"] or "LONG").upper(),
                    "quantity": abs(int(float(row["quantity"] or 0))),
                    "entry_price": float(row["entry_price"] or 0.0),
                    "stop_loss": float(stop) if stop not in (None, "") else None,
                }
            conn.close()
        except Exception:
            return {}
        return risk_map

    def _load_open_option_strategy_rows(self):
        """
        Load open option positions for strategy exposure reporting.

        These are kept separate from equity exposure because options use
        defined-risk sizing rather than spot market value.
        """
        rows = []
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(
                """
                SELECT ticker, underlying_strategy, underlying_direction,
                       structure_type, contracts, entry_net_price, max_risk_usd
                FROM option_positions
                WHERE UPPER(COALESCE(state, 'OPEN')) = 'OPEN'
                ORDER BY opened_at ASC, id ASC
                """
            )
            rows = cur.fetchall()
            conn.close()
        except Exception:
            return []
        return list(rows)

    def calculate_sector_exposure(self):
        """Calculate direction-aware exposure by sector."""
        positions = self.get_current_positions()

        if not positions:
            return {
                'sectors': {},
                'total_value': 0,
                'gross_total_value': 0,
                'long_total_value': 0,
                'short_total_value': 0,
                'net_total_value': 0,
            }

        sector_exposure = {}
        gross_total_value = sum(abs(pos['market_value']) for pos in positions)
        long_total_value = sum(abs(pos['market_value']) for pos in positions if str(pos.get('direction', 'LONG')).upper() == 'LONG')
        short_total_value = sum(abs(pos['market_value']) for pos in positions if str(pos.get('direction', 'LONG')).upper() == 'SHORT')
        net_total_value = long_total_value - short_total_value

        for pos in positions:
            sector = self.sector_map.get(pos['symbol'], 'Unknown')
            direction = str(pos.get('direction', 'LONG')).upper()
            market_value = abs(float(pos['market_value']))

            if sector not in sector_exposure:
                sector_exposure[sector] = {
                    'value': 0,
                    'gross_value': 0,
                    'long_value': 0,
                    'short_value': 0,
                    'net_value': 0,
                    'tickers': [],
                    'long_tickers': [],
                    'short_tickers': [],
                    'percentage': 0,
                    'gross_pct': 0,
                    'long_pct': 0,
                    'short_pct': 0,
                    'net_pct': 0,
                }

            sector_exposure[sector]['value'] += market_value
            sector_exposure[sector]['gross_value'] += market_value
            if direction == 'SHORT':
                sector_exposure[sector]['short_value'] += market_value
                sector_exposure[sector]['net_value'] -= market_value
                sector_exposure[sector]['short_tickers'].append(pos['symbol'])
            else:
                sector_exposure[sector]['long_value'] += market_value
                sector_exposure[sector]['net_value'] += market_value
                sector_exposure[sector]['long_tickers'].append(pos['symbol'])
            sector_exposure[sector]['tickers'].append(pos['symbol'])

        # Calculate percentages using gross exposure as the concentration base.
        for sector in sector_exposure:
            gross_value = sector_exposure[sector]['gross_value']
            long_value = sector_exposure[sector]['long_value']
            short_value = sector_exposure[sector]['short_value']
            net_value = sector_exposure[sector]['net_value']
            if gross_total_value > 0:
                gross_pct = gross_value / gross_total_value * 100
                long_pct = long_value / gross_total_value * 100
                short_pct = short_value / gross_total_value * 100
                net_pct = net_value / gross_total_value * 100
            else:
                gross_pct = long_pct = short_pct = net_pct = 0
            sector_exposure[sector]['percentage'] = gross_pct
            sector_exposure[sector]['gross_pct'] = gross_pct
            sector_exposure[sector]['long_pct'] = long_pct
            sector_exposure[sector]['short_pct'] = short_pct
            sector_exposure[sector]['net_pct'] = net_pct

        return {
            'sectors': sector_exposure,
            'total_value': gross_total_value,
            'gross_total_value': gross_total_value,
            'long_total_value': long_total_value,
            'short_total_value': short_total_value,
            'net_total_value': net_total_value,
        }

    def calculate_strategy_exposure(self):
        """
        Calculate direction-aware exposure by strategy.

        Equity exposure is reported on broker market value.
        Options exposure is reported separately on defined-risk / max-risk basis.
        """
        trade_meta = self._load_open_trade_risk_map()
        positions = self.get_current_positions()
        option_rows = self._load_open_option_strategy_rows()

        def _empty_bucket():
            return {
                'gross_value': 0.0,
                'long_value': 0.0,
                'short_value': 0.0,
                'net_value': 0.0,
                'gross_pct': 0.0,
                'long_pct': 0.0,
                'short_pct': 0.0,
                'net_pct': 0.0,
                'tickers': [],
                'long_tickers': [],
                'short_tickers': [],
            }

        equity_strategies = {}
        equity_gross = 0.0
        equity_long = 0.0
        equity_short = 0.0

        for pos in positions:
            ticker = str(pos.get('symbol') or '').upper()
            meta = trade_meta.get(ticker) or {}
            strategy = str(meta.get('strategy') or 'UNKNOWN_EQUITY').upper()
            direction = str(meta.get('direction') or pos.get('direction') or 'LONG').upper()
            value = abs(float(pos.get('market_value', 0.0) or 0.0))
            if strategy not in equity_strategies:
                equity_strategies[strategy] = _empty_bucket()
            bucket = equity_strategies[strategy]
            bucket['gross_value'] += value
            bucket['tickers'].append(ticker)
            equity_gross += value
            if direction == 'SHORT':
                bucket['short_value'] += value
                bucket['short_tickers'].append(ticker)
                bucket['net_value'] -= value
                equity_short += value
            else:
                bucket['long_value'] += value
                bucket['long_tickers'].append(ticker)
                bucket['net_value'] += value
                equity_long += value

        for bucket in equity_strategies.values():
            if equity_gross > 0:
                bucket['gross_pct'] = (bucket['gross_value'] / equity_gross) * 100.0
                bucket['long_pct'] = (bucket['long_value'] / equity_gross) * 100.0
                bucket['short_pct'] = (bucket['short_value'] / equity_gross) * 100.0
                bucket['net_pct'] = (bucket['net_value'] / equity_gross) * 100.0

        option_strategies = {}
        options_gross = 0.0
        options_long = 0.0
        options_short = 0.0

        for row in option_rows:
            strategy = f"OPTIONS:{str(row['underlying_strategy'] or 'UNKNOWN').upper()}"
            direction = str(row['underlying_direction'] or 'LONG').upper()
            ticker = str(row['ticker'] or '').upper()
            max_risk = row['max_risk_usd']
            entry_net = row['entry_net_price']
            contracts = int(row['contracts'] or 0)
            value = float(max_risk or 0.0)
            if value <= 0 and entry_net is not None and contracts > 0:
                value = abs(float(entry_net) * contracts * 100.0)
            if strategy not in option_strategies:
                option_strategies[strategy] = _empty_bucket()
            bucket = option_strategies[strategy]
            bucket['gross_value'] += value
            bucket['tickers'].append(ticker)
            options_gross += value
            if direction == 'SHORT':
                bucket['short_value'] += value
                bucket['short_tickers'].append(ticker)
                bucket['net_value'] -= value
                options_short += value
            else:
                bucket['long_value'] += value
                bucket['long_tickers'].append(ticker)
                bucket['net_value'] += value
                options_long += value

        for bucket in option_strategies.values():
            if options_gross > 0:
                bucket['gross_pct'] = (bucket['gross_value'] / options_gross) * 100.0
                bucket['long_pct'] = (bucket['long_value'] / options_gross) * 100.0
                bucket['short_pct'] = (bucket['short_value'] / options_gross) * 100.0
                bucket['net_pct'] = (bucket['net_value'] / options_gross) * 100.0

        return {
            'equity': {
                'strategies': equity_strategies,
                'gross_total_value': equity_gross,
                'long_total_value': equity_long,
                'short_total_value': equity_short,
                'net_total_value': equity_long - equity_short,
                'basis': 'market_value',
            },
            'options': {
                'strategies': option_strategies,
                'gross_total_value': options_gross,
                'long_total_value': options_long,
                'short_total_value': options_short,
                'net_total_value': options_long - options_short,
                'basis': 'max_risk_usd',
            },
        }

    def calculate_portfolio_heat(self):
        """
        Calculate total portfolio risk exposure using open-trade stop risk.

        Priority:
        1. Use stop-based risk from open trades in the local DB.
        2. If a broker-held position has no tracked stop, apply an explicit
           conservative fallback based on gross market value.
        """
        positions = self.get_current_positions()
        trade_risk_map = self._load_open_trade_risk_map()

        try:
            account = self.trading_client.get_account()
            portfolio_value = float(account.portfolio_value)
        except:
            portfolio_value = 0

        if not positions:
            return {
                'total_heat': 0,
                'position_count': 0,
                'avg_risk_per_position': 0,
                'portfolio_value': portfolio_value,
                'measured_heat': 0,
                'estimated_heat': 0,
                'coverage_pct': 100.0,
                'estimated_positions': 0,
                'fallback_assumption_pct': round(self.UNKNOWN_STOP_FALLBACK_PCT * 100.0, 2),
                'positions': [],
            }

        def _risk_pct(entry_price, stop_loss, quantity, direction):
            try:
                entry = float(entry_price or 0)
                stop = float(stop_loss or 0)
                qty = abs(float(quantity or 0))
                if entry <= 0 or stop <= 0 or qty <= 0 or portfolio_value <= 0:
                    return None
                side = str(direction or "LONG").upper()
                per_share = (stop - entry) if side == "SHORT" else (entry - stop)
                risk_dollars = max(0.0, per_share) * qty
                if risk_dollars <= 0:
                    return None
                return (risk_dollars / portfolio_value) * 100.0
            except Exception:
                return None

        measured_heat = 0.0
        estimated_heat = 0.0
        measured_positions = 0
        estimated_positions = 0
        risk_rows = []

        for pos in positions:
            ticker = str(pos['symbol']).upper()
            tracked = trade_risk_map.get(ticker) or {}
            direction = tracked.get("direction") or pos.get("direction") or "LONG"
            quantity = tracked.get("quantity") or pos.get("qty") or 0
            entry_price = tracked.get("entry_price") or pos.get("avg_entry") or 0
            stop_loss = tracked.get("stop_loss")

            risk_pct = _risk_pct(entry_price, stop_loss, quantity, direction)
            risk_source = "tracked_stop"
            if risk_pct is None:
                gross_value = abs(float(pos.get("market_value", 0.0) or 0.0))
                risk_pct = ((gross_value * self.UNKNOWN_STOP_FALLBACK_PCT) / portfolio_value) * 100.0 if portfolio_value > 0 else 0.0
                estimated_heat += risk_pct
                estimated_positions += 1
                risk_source = "estimated_fallback"
            else:
                measured_heat += risk_pct
                measured_positions += 1

            risk_rows.append({
                "ticker": ticker,
                "direction": direction,
                "risk_pct": round(risk_pct, 2),
                "risk_source": risk_source,
            })

        total_heat = measured_heat + estimated_heat
        coverage_pct = (measured_positions / len(positions) * 100.0) if positions else 100.0

        return {
            'total_heat': round(total_heat, 1),
            'position_count': len(positions),
            'avg_risk_per_position': round(total_heat / len(positions), 2) if positions else 0,
            'portfolio_value': portfolio_value,
            'measured_heat': round(measured_heat, 1),
            'estimated_heat': round(estimated_heat, 1),
            'coverage_pct': round(coverage_pct, 1),
            'estimated_positions': estimated_positions,
            'fallback_assumption_pct': round(self.UNKNOWN_STOP_FALLBACK_PCT * 100.0, 2),
            'positions': risk_rows,
        }

    # -----------------------------
    # Correlation engine (FIXED)
    # -----------------------------
    def _extract_aligned_returns(self, ticker1, ticker2, days=60):
        """
        Fetch bars and align by timestamp so returns line up correctly.
        Returns two equal-length return lists.
        """
        bars1 = self.data_feed.get_daily_bars(ticker1, days_back=days)
        bars2 = self.data_feed.get_daily_bars(ticker2, days_back=days)

        if not bars1 or not bars2 or len(bars1) < 25 or len(bars2) < 25:
            return None, None

        # Build timestamp->close maps
        m1 = {b['timestamp']: b['close'] for b in bars1}
        m2 = {b['timestamp']: b['close'] for b in bars2}

        common_ts = sorted(set(m1.keys()) & set(m2.keys()))
        if len(common_ts) < 25:
            return None, None

        closes1 = [m1[t] for t in common_ts]
        closes2 = [m2[t] for t in common_ts]

        # returns
        r1 = []
        r2 = []
        for i in range(1, len(common_ts)):
            if closes1[i-1] == 0 or closes2[i-1] == 0:
                continue
            r1.append((closes1[i] - closes1[i-1]) / closes1[i-1])
            r2.append((closes2[i] - closes2[i-1]) / closes2[i-1])

        if len(r1) < 20 or len(r2) < 20:
            return None, None

        # ensure equal length
        n = min(len(r1), len(r2))
        return r1[-n:], r2[-n:]

    def calculate_position_correlation(self, ticker1, ticker2, days=60):
        """
        Calculate Pearson correlation between two tickers (returns).
        Returns: -1.0 to 1.0
        Uses correct covariance/(std1*std2).
        """
        r1, r2 = self._extract_aligned_returns(ticker1, ticker2, days=days)
        if not r1 or not r2:
            return None

        n = min(len(r1), len(r2))
        r1 = r1[-n:]
        r2 = r2[-n:]

        if n < 10:
            return None

        mean1 = sum(r1) / n
        mean2 = sum(r2) / n

        # sample covariance
        cov = sum((r1[i] - mean1) * (r2[i] - mean2) for i in range(n)) / (n - 1)

        try:
            std1 = statistics.stdev(r1)
            std2 = statistics.stdev(r2)
        except:
            return None

        if std1 == 0 or std2 == 0:
            return None

        corr = cov / (std1 * std2)
        # clamp for safety
        corr = max(-1.0, min(1.0, corr))
        return round(corr, 3)

    def check_correlation_with_portfolio(self, new_ticker):
        """
        Check if new ticker is highly correlated with existing positions
        EXCLUDES self-correlation when the ticker is already held.
        Uses ABS correlation for risk.
        """
        positions = self.get_current_positions()

        if not positions:
            return {
                'has_conflict': False,
                'correlations': {},
                'max_correlation': 0,
                'correlated_with': []
            }

        correlations = {}

        for pos in positions:
            existing_ticker = pos['symbol']

            # Skip self-correlation if already holding same ticker
            if existing_ticker.upper() == new_ticker.upper():
                continue

            corr = self.calculate_position_correlation(new_ticker, existing_ticker)
            if corr is not None:
                correlations[existing_ticker] = corr

        # Use absolute correlation for risk checks
        max_abs_corr = max((abs(v) for v in correlations.values()), default=0)

        # High correlation threshold
        has_conflict = max_abs_corr > 0.7

        return {
            'has_conflict': has_conflict,
            'correlations': correlations,
            'max_correlation': round(max_abs_corr, 3),
            'correlated_with': [k for k, v in correlations.items() if abs(v) > 0.7]
        }

    # -----------------------------
    # Vote logic (HARD vs SOFT)
    # -----------------------------
    def vote_on_trade(self, ticker, direction=None):
        """
        Agent voting function for Veto Council.
        Returns: APPROVE / CAUTION / VETO
        Adds remediation + optional position sizing.
        """

        positions = self.get_current_positions()
        held_symbols = {p['symbol'].upper() for p in positions}
        already_held = ticker.upper() in held_symbols

        # Sector concentration
        sector_analysis = self.calculate_sector_exposure()
        new_sector = self.sector_map.get(ticker, 'Unknown')
        new_direction = str(direction or "LONG").upper()

        current_sector_pct = 0.0
        current_sector_long_pct = 0.0
        current_sector_short_pct = 0.0
        if new_sector in sector_analysis['sectors']:
            sector_row = sector_analysis['sectors'][new_sector]
            current_sector_pct = sector_row['gross_pct']
            current_sector_long_pct = sector_row['long_pct']
            current_sector_short_pct = sector_row['short_pct']
        directional_sector_pct = current_sector_short_pct if new_direction == "SHORT" else current_sector_long_pct

        # Correlation
        correlation_check = self.check_correlation_with_portfolio(ticker)

        # Heat
        heat = self.calculate_portfolio_heat()

        # --- HARD VETO: sector cap ---
        if current_sector_pct > self.SECTOR_HARD_CAP_PCT:
            required_reduction_pct = max(0.0, round(current_sector_pct - self.SECTOR_HARD_CAP_PCT, 1))
            return {
                'vote': 'VETO',
                'hard_veto': True,
                'veto_type': 'SECTOR_CONCENTRATION',
                'reason': (
                    f"Sector over-concentrated: {new_sector} gross {current_sector_pct:.1f}% "
                    f"(long {current_sector_long_pct:.1f}% / short {current_sector_short_pct:.1f}%)"
                ),
                'score': 0,
                'sector_gross_pct': round(current_sector_pct, 1),
                'sector_directional_pct': round(directional_sector_pct, 1),
                'remediation': {
                    'action': 'TRIM_SECTOR',
                    'target_sector': new_sector,
                    'required_reduction_pct': required_reduction_pct,
                    'target_cap_pct': self.SECTOR_HARD_CAP_PCT,
                    'gross_pct': round(current_sector_pct, 1),
                    'long_pct': round(current_sector_long_pct, 1),
                    'short_pct': round(current_sector_short_pct, 1),
                }
            }

        # --- HARD VETO: correlation (skip if adding to same ticker already held) ---
        if correlation_check['has_conflict'] and not already_held:
            correlated = ', '.join(correlation_check.get('correlated_with', []))
            return {
                'vote': 'VETO',
                'hard_veto': True,
                'veto_type': 'HIGH_CORRELATION',
                'reason': f"High correlation ({correlation_check['max_correlation']:.2f}) with {correlated}",
                'score': 0,
                'remediation': {
                    'action': 'REDUCE_CORRELATED',
                    'correlated_with': correlation_check.get('correlated_with', []),
                    'max_allowed_corr': 0.75
                }
            }

        # --- HARD VETO: heat cap ---
        if heat['total_heat'] > self.HEAT_HARD_CAP:
            required_reduction_pct = max(0.0, round(heat['total_heat'] - self.HEAT_HARD_CAP, 1))
            return {
                'vote': 'VETO',
                'hard_veto': True,
                'veto_type': 'PORTFOLIO_HEAT',
                'reason': f"Portfolio heat too high: {heat['total_heat']:.1f}% (max {self.HEAT_HARD_CAP:.0f}%)",
                'score': 0,
                'remediation': {
                    'action': 'REDUCE_HEAT',
                    'max_total_heat_pct': self.HEAT_HARD_CAP,
                    'required_reduction_pct': required_reduction_pct
                }
            }

        # --- SOFT constraints => CAUTION with sizing ---
        # Sector warn
        if current_sector_pct > self.SECTOR_SOFT_WARN_PCT:
            return {
                'vote': 'CAUTION',
                'hard_veto': False,
                'veto_type': 'SECTOR_ELEVATED',
                'reason': (
                    f"Sector concentration elevated: {new_sector} gross {current_sector_pct:.1f}% "
                    f"(long {current_sector_long_pct:.1f}% / short {current_sector_short_pct:.1f}%)"
                ),
                'score': 65,
                'position_size_multiplier': 0.5,
                'sector_gross_pct': round(current_sector_pct, 1),
                'sector_directional_pct': round(directional_sector_pct, 1),
                'remediation': {
                    'action': 'REDUCE_SIZE',
                    'suggested_multiplier': 0.5,
                    'gross_pct': round(current_sector_pct, 1),
                    'long_pct': round(current_sector_long_pct, 1),
                    'short_pct': round(current_sector_short_pct, 1),
                }
            }

        # Moderate correlation
        max_abs_corr = correlation_check.get('max_correlation', 0.0)
        if max_abs_corr >= self.CORR_SOFT_ABS:
            correlated = ', '.join(correlation_check.get('correlated_with', []))
            return {
                'vote': 'CAUTION',
                'hard_veto': False,
                'veto_type': 'CORR_ELEVATED',
                'reason': f"Moderate correlation dependency (abs {max_abs_corr:.2f}) vs {correlated}",
                'score': 70,
                'position_size_multiplier': 0.25,
                'remediation': {
                    'action': 'REDUCE_SIZE',
                    'suggested_multiplier': 0.25
                }
            }

        # Heat warn
        if heat['total_heat'] > self.HEAT_SOFT_WARN:
            return {
                'vote': 'CAUTION',
                'hard_veto': False,
                'veto_type': 'HEAT_ELEVATED',
                'reason': f"Portfolio heat elevated: {heat['total_heat']:.1f}%",
                'score': 75,
                'position_size_multiplier': 0.5,
                'remediation': {
                    'action': 'REDUCE_SIZE',
                    'suggested_multiplier': 0.5
                }
            }

        # --- APPROVE ---
        return {
            'vote': 'APPROVE',
            'hard_veto': False,
            'veto_type': None,
            'reason': (
                f"Portfolio risk acceptable: {new_sector} gross {current_sector_pct:.1f}% "
                f"(long {current_sector_long_pct:.1f}% / short {current_sector_short_pct:.1f}%), "
                f"heat {heat['total_heat']:.1f}%"
            ),
            'score': 85,
            'position_size_multiplier': 1.0,
            'sector_gross_pct': round(current_sector_pct, 1),
            'sector_directional_pct': round(directional_sector_pct, 1),
        }

    # -----------------------------
    # Display helper (optional)
    # -----------------------------
    def display_portfolio_analysis(self, new_ticker=None, new_direction=None):
        """Display complete portfolio analysis"""
        print(f"\n{'='*80}")
        print(f"💼 PORTFOLIO RISK ANALYSIS")
        if new_ticker:
            print(f"Evaluating: {new_ticker}")
        print(f"{'='*80}")

        positions = self.get_current_positions()
        print(f"\n📊 CURRENT POSITIONS: {len(positions)}")

        for pos in positions:
            pnl_emoji = "🟢" if pos['unrealized_pl'] > 0 else "🔴"
            print(f"  {pnl_emoji} {pos['symbol']}: {pos['qty']} shares @ ${pos['avg_entry']:.2f}")
            print(f"     Current: ${pos['current_price']:.2f} | P&L: ${pos['unrealized_pl']:.2f} ({pos['unrealized_pl_pct']:+.2f}%)")

        sector_analysis = self.calculate_sector_exposure()
        print(f"\n🎯 SECTOR EXPOSURE:")
        print(
            f"  Gross: ${sector_analysis.get('gross_total_value', 0):,.0f} | "
            f"Long: ${sector_analysis.get('long_total_value', 0):,.0f} | "
            f"Short: ${sector_analysis.get('short_total_value', 0):,.0f} | "
            f"Net: ${sector_analysis.get('net_total_value', 0):,.0f}"
        )

        sorted_sectors = sorted(sector_analysis['sectors'].items(),
                               key=lambda x: x[1]['gross_pct'],
                               reverse=True)

        for sector, data in sorted_sectors:
            tickers = ', '.join(data['tickers'])
            emoji = "⚠️" if data['gross_pct'] > self.SECTOR_SOFT_WARN_PCT else "✅"
            print(
                f"  {emoji} {sector}: gross {data['gross_pct']:.1f}% | "
                f"long {data['long_pct']:.1f}% | short {data['short_pct']:.1f}% | "
                f"net {data['net_pct']:+.1f}% ({tickers})"
            )
            if data['long_tickers']:
                print(f"     LONG: {', '.join(data['long_tickers'])}")
            if data['short_tickers']:
                print(f"     SHORT: {', '.join(data['short_tickers'])}")

        strategy_analysis = self.calculate_strategy_exposure()
        equity_strategies = strategy_analysis.get('equity', {})
        option_strategies = strategy_analysis.get('options', {})

        print(f"\n🧭 STRATEGY EXPOSURE (EQUITY)")
        print(
            f"  Gross: ${equity_strategies.get('gross_total_value', 0):,.0f} | "
            f"Long: ${equity_strategies.get('long_total_value', 0):,.0f} | "
            f"Short: ${equity_strategies.get('short_total_value', 0):,.0f} | "
            f"Net: ${equity_strategies.get('net_total_value', 0):,.0f}"
        )
        eq_rows = sorted(
            (equity_strategies.get('strategies') or {}).items(),
            key=lambda x: x[1]['gross_pct'],
            reverse=True,
        )
        if not eq_rows:
            print("  — No equity strategy exposure")
        for strategy, data in eq_rows:
            print(
                f"  • {strategy}: gross {data['gross_pct']:.1f}% | "
                f"long {data['long_pct']:.1f}% | short {data['short_pct']:.1f}% | "
                f"net {data['net_pct']:+.1f}% ({', '.join(data['tickers'])})"
            )

        print(f"\n🧭 STRATEGY EXPOSURE (OPTIONS, DEFINED-RISK BASIS)")
        print(
            f"  Gross Risk: ${option_strategies.get('gross_total_value', 0):,.0f} | "
            f"Long Bias: ${option_strategies.get('long_total_value', 0):,.0f} | "
            f"Short Bias: ${option_strategies.get('short_total_value', 0):,.0f} | "
            f"Net: ${option_strategies.get('net_total_value', 0):,.0f}"
        )
        opt_rows = sorted(
            (option_strategies.get('strategies') or {}).items(),
            key=lambda x: x[1]['gross_pct'],
            reverse=True,
        )
        if not opt_rows:
            print("  — No options strategy exposure")
        for strategy, data in opt_rows:
            print(
                f"  • {strategy}: gross {data['gross_pct']:.1f}% | "
                f"long {data['long_pct']:.1f}% | short {data['short_pct']:.1f}% | "
                f"net {data['net_pct']:+.1f}% ({', '.join(data['tickers'])})"
            )

        heat = self.calculate_portfolio_heat()
        print(f"\n🔥 PORTFOLIO HEAT:")
        print(f"  Total Risk: {heat['total_heat']:.1f}%")
        print(f"  Positions: {heat['position_count']}")
        print(f"  Avg Risk/Position: {heat['avg_risk_per_position']:.2f}%")
        print(f"  Measured Risk: {heat.get('measured_heat', 0):.1f}%")
        print(f"  Estimated Risk: {heat.get('estimated_heat', 0):.1f}%")
        print(f"  Stop Coverage: {heat.get('coverage_pct', 0):.1f}%")
        print(f"  Fallback Assumption: {heat.get('fallback_assumption_pct', 0):.2f}% of gross value")

        heat_emoji = "🟢" if heat['total_heat'] < 10 else "🟡" if heat['total_heat'] < self.HEAT_HARD_CAP else "🔴"
        print(f"  {heat_emoji} Status: {'Safe' if heat['total_heat'] < 10 else 'Elevated' if heat['total_heat'] < self.HEAT_HARD_CAP else 'DANGER'}")

        if new_ticker:
            print(f"\n🔍 EVALUATING NEW POSITION: {new_ticker}")

            new_sector = self.sector_map.get(new_ticker, 'Unknown')
            print(f"  Sector: {new_sector}")

            corr_check = self.check_correlation_with_portfolio(new_ticker)
            print(f"\n📈 CORRELATION (ABS-DEPENDENCY) TOP PAIRS:")

            correlations = corr_check.get('correlations', {})
            if correlations:
                top = sorted(correlations.items(), key=lambda x: abs(x[1]), reverse=True)[:5]
                for sym, corr in top[:5]:
                    corr_emoji = "🔴" if abs(corr) >= self.CORR_HARD_ABS else "🟡" if abs(corr) >= self.CORR_SOFT_ABS else "🟢"
                    print(f"  {corr_emoji} vs {sym}: {corr:+.3f} (abs {abs(corr):.3f})")
            else:
                print("  ✅ No correlation data available")

            vote = self.vote_on_trade(new_ticker, direction=new_direction)
            print(f"\n🗳️  VETO COUNCIL VOTE: {vote['vote']}")
            print(f"  Reason: {vote['reason']}")
            if vote.get('remediation'):
                print(f"  Remediation: {vote['remediation']}")
            if vote.get('position_size_multiplier') is not None:
                print(f"  Suggested Size: {vote.get('position_size_multiplier', 1.0)}x")
            print(f"  Score: {vote.get('score', 0)}/100")

        print(f"\n{'='*80}\n")


def test_portfolio_manager():
    print("🚀 Testing Portfolio Manager...\n")

    manager = PortfolioManager()
    manager.display_portfolio_analysis()

    print("\n" + "="*80)
    print("TESTING: What if we wanted to add SATL?")
    print("="*80)
    manager.display_portfolio_analysis("SATL")


if __name__ == "__main__":
    test_portfolio_manager()
