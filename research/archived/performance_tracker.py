import sqlite3
from datetime import datetime
from alpaca.trading.client import TradingClient
from config import TradingConfig
import json

class PerformanceTracker:
    """
    Track all trades and calculate performance metrics
    
    Features:
    - Logs every trade entry/exit
    - Calculates win rate, profit factor, avg R:R
    - Tracks by sector, confidence, strategy
    - Compares actual vs predicted results
    - Generates performance reports
    """
    
    def __init__(self):
        self.config = TradingConfig()
        
        # Trading client to check positions
        self.trading_client = TradingClient(
            self.config.ALPACA_API_KEY,
            self.config.ALPACA_SECRET_KEY,
            paper=True
        )
        
        # Database
        self.db_path = "trading_performance.db"
        self._init_database()
        self._ensure_trades_analytics_columns()

        print("✅ Performance Tracker initialized")
        print(f"📊 Database: {self.db_path}")
    
    def _init_database(self):
        """Initialize SQLite database with tables"""
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Trades table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                entry_date TEXT NOT NULL,
                entry_price REAL NOT NULL,
                quantity INTEGER NOT NULL,
                stop_loss REAL,
                target_price REAL,
                exit_date TEXT,
                exit_price REAL,
                exit_reason TEXT,
                pnl REAL,
                pnl_percent REAL,
                win BOOLEAN,
                risk_amount REAL,
                reward_amount REAL,
                actual_rr REAL,
                predicted_rr REAL,
                sector TEXT,
                confidence TEXT,
                confluence_score REAL,
                greek_score REAL,
                hold_days INTEGER,
                notes TEXT
            )
        ''')
        
        # Daily stats table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS daily_stats (
                date TEXT PRIMARY KEY,
                total_trades INTEGER,
                wins INTEGER,
                losses INTEGER,
                win_rate REAL,
                total_pnl REAL,
                avg_win REAL,
                avg_loss REAL,
                profit_factor REAL,
                largest_win REAL,
                largest_loss REAL
            )
        ''')

        # Runs table (one row per scan)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                timestamp TEXT NOT NULL,
                mode TEXT NOT NULL,                 -- AUTO_TRADER / MASTER_ALIGNMENT / DRYRUN
                watchlist_size INTEGER NOT NULL,
                market_session TEXT,                -- PRE / REG / AFTER / CLOSED
                regime_status TEXT,                 -- UPTREND / SIDEWAYS / DOWNTREND
                regime_volatility TEXT,             -- NORMAL / HIGH
                notes TEXT
            )
        ''')

        # Decisions table (one row per ticker decision)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                ticker TEXT NOT NULL,
                strategy TEXT,
                direction TEXT,
                shares INTEGER,

                market_session TEXT,

                signal TEXT,
                confluence_score REAL,

                entry_price REAL,
                stop_loss REAL,
                target_price REAL,
                rr REAL,

                council_decision TEXT,              -- EXECUTE / REJECT / CAUTION
                council_reason TEXT,
                approve_count INTEGER,
                caution_count INTEGER,
                veto_count INTEGER,
                avg_score REAL,

                veto_reasons_json TEXT,
                votes_json TEXT,

                sentiment_score REAL,
                sentiment_conf REAL,
                sentiment_label TEXT,

                regime_status TEXT,
                regime_volatility TEXT,

                order_submitted INTEGER DEFAULT 0,  -- 0/1
                order_id TEXT,
                position_opened INTEGER DEFAULT 0,  -- 0/1
                execution_denied INTEGER DEFAULT 0, -- 0/1
                execution_deny_reason TEXT,

                notes TEXT,

                FOREIGN KEY(run_id) REFERENCES runs(run_id)
            )
        ''')
        self._ensure_decisions_schema(conn)

        # Order events table (broker truth)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS order_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                ticker TEXT NOT NULL,
                order_id TEXT NOT NULL,
                event_type TEXT NOT NULL,           -- SUBMITTED / FILLED / CANCELED / REJECTED
                qty REAL,
                avg_fill_price REAL,
                status TEXT,
                raw_json TEXT
            )
        ''')

        # System events table (operational health telemetry)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS system_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT,
                timestamp TEXT NOT NULL,
                component TEXT NOT NULL,
                severity TEXT NOT NULL,
                error_type TEXT,
                message TEXT NOT NULL,
                details_json TEXT
            )
        ''')

        # Helpful indexes (speed)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_decisions_run_id ON decisions(run_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_decisions_ticker_time ON decisions(ticker, timestamp)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_decisions_strategy ON decisions(strategy)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_decisions_direction ON decisions(direction)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_decisions_timestamp ON decisions(timestamp)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_order_events_order_id ON order_events(order_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_system_events_run_id ON system_events(run_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_system_events_component_time ON system_events(component, timestamp)")

        conn.commit()
        conn.close()
        
        print("✅ Database tables initialized")

    def _ensure_decisions_schema(self, conn):
        """One-time additive migration for decisions table columns."""
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(decisions)")
        existing = {row[1] for row in cur.fetchall()}

        if "strategy" not in existing:
            cur.execute("ALTER TABLE decisions ADD COLUMN strategy TEXT")
        if "direction" not in existing:
            cur.execute("ALTER TABLE decisions ADD COLUMN direction TEXT")
        if "shares" not in existing:
            cur.execute("ALTER TABLE decisions ADD COLUMN shares INTEGER")

        conn.commit()

    def _ensure_trades_analytics_columns(self):
        """Additive migration — adds exit attribution columns to trades table.

        Also backfills direction='LONG' for legacy imported trades (pre-V3
        execution path).  Strategy is left NULL for those rows — it cannot be
        derived from available data and fabricating it would corrupt analytics.
        """
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(trades)")
        existing = {row[1] for row in cur.fetchall()}

        additions = [
            ("strategy",        "TEXT"),
            ("direction",       "TEXT"),
            ("status",          "TEXT"),
            ("initial_stop_loss","REAL"),
            ("score",           "REAL"),
            ("grade",           "TEXT"),
            ("primary_pathway", "TEXT"),
            # Phase 3 — thesis
            ("thesis_status",          "TEXT"),
            ("thesis_strength",        "REAL"),
            ("thesis_reason",          "TEXT"),
            ("thesis_updated_at",      "TEXT"),
            # Phase 3 — forecast (entry snapshot)
            ("forecast_p_win",         "REAL"),
            ("forecast_expected_return","REAL"),
            ("forecast_expected_rr",   "REAL"),
            ("forecast_horizon_days",  "INTEGER"),
            ("forecast_conf_low",      "REAL"),
            ("forecast_conf_high",     "REAL"),
            ("forecast_model_version", "TEXT"),
            # Phase 3 — forecast (exit attribution)
            ("forecast_error",         "REAL"),
            # Phase 3 — enrichment signals
            ("revision_score",         "REAL"),
            ("insider_cluster_score",  "REAL"),
            ("squeeze_risk_score",     "REAL"),
            ("volume_profile_quality", "TEXT"),
            # Phase 3 — extended P&L aliases (used by forecast engine)
            ("pnl_pct",                "REAL"),
            ("pnl_usd",                "REAL"),
            ("composite_score",        "REAL"),
            ("entry_time",             "TEXT"),
            ("exit_time",              "TEXT"),
            ("pathway",                "TEXT"),
            # Phase 4.3/4.4 — pilot telemetry
            ("is_pilot",               "INTEGER DEFAULT 0"),
            ("pilot_policy",           "TEXT"),
            ("pilot_threshold",        "REAL"),
            ("analytics_excluded",     "INTEGER DEFAULT 0"),
            ("analytics_exclude_reason","TEXT"),
            ("analytics_excluded_at",  "TEXT"),
            # Phase 5 — execution/scanner alignment + emergency exit telemetry
            ("scanner_rr",             "REAL"),
            ("circuit_breaker_triggered", "INTEGER DEFAULT 0"),
            # Phase 5.1 — trailing stop lifecycle telemetry
            ("trail_stop_activated",   "INTEGER DEFAULT 0"),
            ("trail_locked_at_r",      "REAL"),
            # Exit provenance — separates economic exit reason from attribution path.
            ("exit_provenance",        "TEXT"),
            ("exit_order_type",        "TEXT"),
            ("exit_price_source",      "TEXT"),
            ("forced_exit_reason",     "TEXT"),
        ]
        for col, col_type in additions:
            if col not in existing:
                cur.execute(f"ALTER TABLE trades ADD COLUMN {col} {col_type}")

        # Backfill direction for legacy Alpaca-imported trades.
        # All existing NULL-direction trades are long equity positions — SHORT
        # positions were never imported via this path.
        cur.execute("UPDATE trades SET direction = 'LONG' WHERE direction IS NULL")
        # Backfill lifecycle status for pre-Phase-3 rows.
        cur.execute("UPDATE trades SET status = 'CLOSED' WHERE exit_date IS NOT NULL AND (status IS NULL OR status = '')")
        cur.execute("UPDATE trades SET status = 'OPEN' WHERE exit_date IS NULL AND (status IS NULL OR status = '')")

        conn.commit()
        conn.close()

    @staticmethod
    def _coerce_rr(value):
        try:
            if value is None:
                return None
            rr = float(value)
            if rr < 0:
                return None
            return rr
        except Exception:
            return None

    @staticmethod
    def _derive_rr_from_levels(direction: str, entry_price, stop_loss, target_price):
        try:
            entry = float(entry_price)
            stop = float(stop_loss)
            target = float(target_price)
            risk = abs(entry - stop)
            if risk <= 0:
                return None
            if str(direction or "LONG").upper() == "SHORT":
                reward = entry - target
            else:
                reward = target - entry
            if reward <= 0:
                return None
            return reward / risk
        except Exception:
            return None
    
    def log_entry(self, ticker, entry_price, quantity, stop_loss, target_price,
                  sector=None, confidence=None, confluence_score=None,
                  greek_score=None, predicted_rr=None, setup_type=None, notes=None,
                  strategy=None, direction=None, score=None, grade=None,
                  primary_pathway=None,
                  scanner_rr=None,
                  initial_stop_loss=None,
                  # Phase 3 — forecast snapshot
                  forecast_p_win=None, forecast_expected_return=None,
                  forecast_expected_rr=None, forecast_horizon_days=None,
                  forecast_conf_low=None, forecast_conf_high=None,
                  forecast_model_version=None,
                  # Phase 3 — enrichment signals
                  revision_score=None, insider_cluster_score=None,
                  squeeze_risk_score=None, volume_profile_quality=None,
                  # Phase 3 — thesis at entry
                  thesis_status=None, thesis_strength=None,
                  composite_score=None, pathway=None,
                  # Phase 4.3/4.4 — pilot flags
                  is_pilot=None, pilot_policy=None, pilot_threshold=None):
        """Log a trade entry with full attribution metadata."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        entry_date = datetime.now().isoformat()

        # Risk/reward amounts — direction-aware
        direction_upper = (direction or 'LONG').upper()
        if direction_upper == 'SHORT':
            risk   = (stop_loss - entry_price) * quantity if stop_loss else None
            reward = (entry_price - target_price) * quantity if target_price else None
        else:
            risk   = (entry_price - stop_loss) * quantity if stop_loss else None
            reward = (target_price - entry_price) * quantity if target_price else None

        # Ensure scanner_rr is always populated for new rows.
        predicted_rr = self._coerce_rr(predicted_rr)
        scanner_rr = self._coerce_rr(scanner_rr)
        if scanner_rr is None:
            scanner_rr = predicted_rr
        if scanner_rr is None:
            scanner_rr = self._derive_rr_from_levels(
                direction=direction_upper,
                entry_price=entry_price,
                stop_loss=stop_loss,
                target_price=target_price,
            )
        if scanner_rr is None:
            scanner_rr = 0.0
        scanner_rr = round(float(scanner_rr), 4)
        if predicted_rr is None:
            predicted_rr = scanner_rr

        # Check which columns exist (handles incremental migrations)
        cursor.execute("PRAGMA table_info(trades)")
        existing_cols = {row[1] for row in cursor.fetchall()}

        base_row = {
            "ticker": ticker, "entry_date": entry_date,
            "entry_price": entry_price, "quantity": quantity,
            "stop_loss": stop_loss, "target_price": target_price,
            "initial_stop_loss": initial_stop_loss if initial_stop_loss is not None else stop_loss,
            "risk_amount": risk, "reward_amount": reward,
            "predicted_rr": predicted_rr, "sector": sector,
            "confidence": confidence, "confluence_score": confluence_score,
            "greek_score": greek_score, "notes": notes, "setup_type": setup_type,
            "strategy": strategy, "direction": direction_upper,
            "status": "OPEN",
            "score": score, "grade": grade, "primary_pathway": primary_pathway,
            # Phase 3
            "composite_score": composite_score or confluence_score,
            "pathway": pathway or primary_pathway,
            "entry_time": entry_date,
            "forecast_p_win": forecast_p_win,
            "forecast_expected_return": forecast_expected_return,
            "forecast_expected_rr": forecast_expected_rr,
            "forecast_horizon_days": forecast_horizon_days,
            "forecast_conf_low": forecast_conf_low,
            "forecast_conf_high": forecast_conf_high,
            "forecast_model_version": forecast_model_version,
            "revision_score": revision_score,
            "insider_cluster_score": insider_cluster_score,
            "squeeze_risk_score": squeeze_risk_score,
            "volume_profile_quality": volume_profile_quality,
            "thesis_status": thesis_status or "INTACT",
            "thesis_strength": thesis_strength,
            # Pilot fields (inserted only when columns exist)
            "is_pilot": is_pilot,
            "pilot_policy": pilot_policy,
            "pilot_threshold": pilot_threshold,
            "scanner_rr": scanner_rr,
        }

        cols = [k for k in base_row if k in existing_cols]
        vals = [base_row[k] for k in cols]

        placeholders = ", ".join(["?"] * len(cols))
        cursor.execute(
            f"INSERT INTO trades ({', '.join(cols)}) VALUES ({placeholders})",
            vals,
        )

        conn.commit()
        trade_id = cursor.lastrowid
        conn.close()

        print(f"✅ Trade logged: {ticker} [{strategy or '?'}] - ID {trade_id}")
        return trade_id
    
    def log_exit(
        self,
        ticker,
        exit_price,
        exit_reason,
        entry_id=None,
        circuit_breaker_triggered=None,
        exit_provenance=None,
        exit_order_type=None,
        exit_price_source=None,
        forced_exit_reason=None,
    ):
        """Log a trade exit. Direction-aware P&L for SHORT trades."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Find the open trade
        if entry_id:
            cursor.execute(
                "SELECT * FROM trades WHERE id = ? AND exit_date IS NULL", (entry_id,)
            )
        else:
            cursor.execute(
                "SELECT * FROM trades WHERE ticker = ? AND exit_date IS NULL "
                "ORDER BY entry_date DESC LIMIT 1",
                (ticker,),
            )

        trade = cursor.fetchone()
        if not trade:
            print(f"❌ No open trade found for {ticker}")
            conn.close()
            return None

        # Map column positions from PRAGMA table_info
        cur2 = conn.cursor()
        cur2.execute("PRAGMA table_info(trades)")
        col_map = {row[1]: row[0] for row in cur2.fetchall()}

        trade_id    = trade[col_map['id']]
        entry_price = trade[col_map['entry_price']]
        quantity    = trade[col_map['quantity']]
        entry_date  = trade[col_map['entry_date']]
        risk_amount = trade[col_map['risk_amount']]
        direction   = (trade[col_map['direction']] or 'LONG').upper() if 'direction' in col_map else 'LONG'

        entry_dt = datetime.fromisoformat(entry_date)
        if entry_dt.tzinfo is not None:
            exit_dt = datetime.now(entry_dt.tzinfo)
        else:
            exit_dt = datetime.now()
        hold_days = max(0, (exit_dt - entry_dt).days)

        # Direction-aware P&L
        if direction == 'SHORT':
            pnl         = (entry_price - exit_price) * quantity
            pnl_percent = ((entry_price - exit_price) / entry_price) * 100
        else:
            pnl         = (exit_price - entry_price) * quantity
            pnl_percent = ((exit_price - entry_price) / entry_price) * 100

        win       = pnl > 0
        actual_rr = pnl / risk_amount if risk_amount and risk_amount != 0 else 0

        # Compute forecast_error if forecast_expected_return was stored at entry
        forecast_error = None
        if "forecast_expected_return" in col_map:
            fe_val = trade[col_map["forecast_expected_return"]]
            if fe_val is not None:
                try:
                    forecast_error = round(pnl_percent - float(fe_val), 4)
                except Exception:
                    pass

        # Build UPDATE with only columns that exist
        update_fields = {
            "exit_date":    exit_dt.isoformat(),
            "exit_price":   exit_price,
            "exit_reason":  exit_reason,
            "exit_provenance": exit_provenance,
            "exit_order_type": exit_order_type,
            "exit_price_source": exit_price_source,
            "forced_exit_reason": forced_exit_reason,
            "pnl":          pnl,
            "pnl_percent":  pnl_percent,
            "win":          win,
            "actual_rr":    actual_rr,
            "hold_days":    hold_days,
            # Phase 3 aliases
            "status":       "CLOSED",
            "pnl_pct":      pnl_percent,
            "pnl_usd":      pnl,
            "exit_time":    exit_dt.isoformat(),
        }
        if circuit_breaker_triggered is not None:
            update_fields["circuit_breaker_triggered"] = int(bool(circuit_breaker_triggered))
        if forecast_error is not None:
            update_fields["forecast_error"] = forecast_error

        cur2.execute("PRAGMA table_info(trades)")
        existing_trade_cols = {row[1] for row in cur2.fetchall()}
        set_clause = ", ".join(f"{k} = ?" for k in update_fields if k in existing_trade_cols)
        set_vals   = [v for k, v in update_fields.items() if k in existing_trade_cols]
        set_vals.append(trade_id)

        cursor.execute(f"UPDATE trades SET {set_clause} WHERE id = ?", set_vals)

        conn.commit()

        # Auto-log to exit_outcomes — guarded: only runs if table and required columns exist
        try:
            cur3 = conn.cursor()
            cur3.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='exit_outcomes'")
            if cur3.fetchone():
                cur3.execute("PRAGMA table_info(exit_outcomes)")
                eo_cols = {row[1] for row in cur3.fetchall()}
                required = {"timestamp", "ticker", "strategy", "setup_type",
                            "entry_price", "exit_price", "exit_reason", "hold_days", "return_pct"}
                if required.issubset(eo_cols):
                    strategy_val  = trade[col_map['strategy']]  if 'strategy'   in col_map else None
                    setup_val     = trade[col_map['setup_type']] if 'setup_type' in col_map else None
                    r_multiple    = actual_rr  # actual_rr is already risk-adjusted
                    cur3.execute("""
                        INSERT INTO exit_outcomes
                            (timestamp, ticker, strategy, setup_type,
                             entry_price, exit_price, exit_reason,
                             hold_days, return_pct, r_multiple)
                        VALUES (?, ?, COALESCE(?, 'UNKNOWN'), COALESCE(?, 'UNKNOWN'),
                                ?, ?, ?, ?, ?, ?)
                    """, (exit_dt.isoformat(), ticker,
                          strategy_val, setup_val,
                          entry_price, exit_price, exit_reason,
                          hold_days, pnl_percent, r_multiple))
                    conn.commit()
        except Exception as _eo_err:
            pass  # exit_outcomes is optional telemetry; never block the trade exit

        conn.close()

        win_emoji = "🟢" if win else "🔴"
        print(f"{win_emoji} Exit logged: {ticker} [{direction}] @ ${exit_price:.2f} "
              f"P&L: ${pnl:.2f} ({pnl_percent:+.2f}%) [{exit_reason}]")

        self._update_daily_stats()
        return trade_id
    
    def import_current_positions(self):
        """
        Import current open positions from Alpaca
        (For your 9 existing trades)
        """
        
        print("\n📥 Importing current positions from Alpaca...")
        
        try:
            positions = self.trading_client.get_all_positions()
            
            if not positions:
                print("💤 No open positions found")
                return
            
            imported = 0
            
            for pos in positions:
                ticker = pos.symbol
                entry_price = float(pos.avg_entry_price)
                quantity = int(pos.qty)
                current_price = float(pos.current_price)
                
                # Check if already logged
                conn = sqlite3.connect(self.db_path)
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT COUNT(*) FROM trades 
                    WHERE ticker = ? AND exit_date IS NULL
                ''', (ticker,))
                exists = cursor.fetchone()[0] > 0
                conn.close()
                
                if exists:
                    print(f"⚠️  {ticker} already logged, skipping")
                    continue
                
                # Estimate stop/target (will need manual update)
                stop_loss = entry_price * 0.90  # Estimate 10% stop
                target_price = entry_price * 1.15  # Estimate 15% target
                predicted_rr = (target_price - entry_price) / (entry_price - stop_loss)
                
                # Log it
                self.log_entry(
                    ticker=ticker,
                    entry_price=entry_price,
                    quantity=quantity,
                    stop_loss=stop_loss,
                    target_price=target_price,
                    predicted_rr=predicted_rr,
                    notes="Imported from existing Alpaca position"
                )
                
                imported += 1
            
            print(f"✅ Imported {imported} position(s)")
            
        except Exception as e:
            print(f"❌ Error importing positions: {e}")
    
    def update_trade_metadata(self, ticker, sector=None, confidence=None, 
                             confluence_score=None, greek_score=None):
        """
        Update metadata for an open trade
        """
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        updates = []
        values = []
        
        if sector:
            updates.append("sector = ?")
            values.append(sector)
        if confidence:
            updates.append("confidence = ?")
            values.append(confidence)
        if confluence_score:
            updates.append("confluence_score = ?")
            values.append(confluence_score)
        if greek_score:
            updates.append("greek_score = ?")
            values.append(greek_score)
        
        if updates:
            values.append(ticker)
            query = f"UPDATE trades SET {', '.join(updates)} WHERE ticker = ? AND exit_date IS NULL"
            cursor.execute(query, values)
            conn.commit()
            print(f"✅ Updated metadata for {ticker}")
        
        conn.close()
    
    def _update_daily_stats(self):
        """Calculate and store daily statistics"""
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        today = datetime.now().date().isoformat()
        
        # Get today's closed trades
        cursor.execute('''
            SELECT pnl, win FROM trades 
            WHERE DATE(exit_date) = ? AND exit_date IS NOT NULL
        ''', (today,))
        
        trades = cursor.fetchall()
        
        if not trades:
            conn.close()
            return
        
        total_trades = len(trades)
        wins = sum(1 for t in trades if t[1])
        losses = total_trades - wins
        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
        
        winning_trades = [t[0] for t in trades if t[1]]
        losing_trades = [t[0] for t in trades if not t[1]]
        
        total_pnl = sum(t[0] for t in trades)
        avg_win = sum(winning_trades) / len(winning_trades) if winning_trades else 0
        avg_loss = sum(losing_trades) / len(losing_trades) if losing_trades else 0
        
        profit_factor = (sum(winning_trades) / abs(sum(losing_trades))) if losing_trades else 0
        
        largest_win = max(winning_trades) if winning_trades else 0
        largest_loss = min(losing_trades) if losing_trades else 0
        
        # Insert or update
        cursor.execute('''
            INSERT OR REPLACE INTO daily_stats 
            (date, total_trades, wins, losses, win_rate, total_pnl, 
             avg_win, avg_loss, profit_factor, largest_win, largest_loss)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (today, total_trades, wins, losses, win_rate, total_pnl,
              avg_win, avg_loss, profit_factor, largest_win, largest_loss))
        
        conn.commit()
        conn.close()
    
    def get_performance_summary(self):
        """Get overall performance summary"""
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # All closed trades
        cursor.execute('''
            SELECT COUNT(*), SUM(CASE WHEN win THEN 1 ELSE 0 END),
                   AVG(pnl), SUM(pnl), AVG(pnl_percent),
                   AVG(hold_days), AVG(actual_rr)
            FROM trades WHERE exit_date IS NOT NULL
        ''')
        
        stats = cursor.fetchone()
        total_trades = stats[0] or 0
        wins = stats[1] or 0
        avg_pnl = stats[2] or 0
        total_pnl = stats[3] or 0
        avg_pnl_pct = stats[4] or 0
        avg_hold_days = stats[5] or 0
        avg_rr = stats[6] or 0
        
        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
        
        # Winning trades
        cursor.execute('''
            SELECT AVG(pnl), AVG(pnl_percent) FROM trades 
            WHERE exit_date IS NOT NULL AND win = 1
        ''')
        win_stats = cursor.fetchone()
        avg_win = win_stats[0] or 0
        avg_win_pct = win_stats[1] or 0
        
        # Losing trades
        cursor.execute('''
            SELECT AVG(pnl), AVG(pnl_percent) FROM trades 
            WHERE exit_date IS NOT NULL AND win = 0
        ''')
        loss_stats = cursor.fetchone()
        avg_loss = loss_stats[0] or 0
        avg_loss_pct = loss_stats[1] or 0
        
        # Profit factor
        cursor.execute('SELECT SUM(pnl) FROM trades WHERE exit_date IS NOT NULL AND win = 1')
        total_wins = cursor.fetchone()[0] or 0
        
        cursor.execute('SELECT SUM(pnl) FROM trades WHERE exit_date IS NOT NULL AND win = 0')
        total_losses = abs(cursor.fetchone()[0] or 0)
        
        profit_factor = (total_wins / total_losses) if total_losses > 0 else 0
        
        # Open positions
        cursor.execute('SELECT COUNT(*) FROM trades WHERE exit_date IS NULL')
        open_trades = cursor.fetchone()[0]
        
        conn.close()
        
        return {
            'total_trades': total_trades,
            'wins': wins,
            'losses': total_trades - wins,
            'win_rate': win_rate,
            'total_pnl': total_pnl,
            'avg_pnl': avg_pnl,
            'avg_pnl_percent': avg_pnl_pct,
            'avg_win': avg_win,
            'avg_win_percent': avg_win_pct,
            'avg_loss': avg_loss,
            'avg_loss_percent': avg_loss_pct,
            'profit_factor': profit_factor,
            'avg_hold_days': avg_hold_days,
            'avg_rr': avg_rr,
            'open_trades': open_trades
        }
    
    def display_performance_report(self):
        """Display formatted performance report"""
        
        stats = self.get_performance_summary()
        
        print(f"\n{'='*80}")
        print(f"📊 PERFORMANCE REPORT")
        print(f"{'='*80}\n")
        
        print(f"📈 OVERALL STATISTICS:")
        print(f"  Total Trades: {stats['total_trades']} ({stats['open_trades']} open)")
        print(f"  Wins: {stats['wins']}")
        print(f"  Losses: {stats['losses']}")
        print(f"  Win Rate: {stats['win_rate']:.1f}%")
        print(f"  Avg Hold Time: {stats['avg_hold_days']:.1f} days")
        
        print(f"\n💰 PROFIT & LOSS:")
        print(f"  Total P&L: ${stats['total_pnl']:.2f}")
        print(f"  Avg P&L: ${stats['avg_pnl']:.2f} ({stats['avg_pnl_percent']:+.2f}%)")
        print(f"  Avg Win: ${stats['avg_win']:.2f} ({stats['avg_win_percent']:+.2f}%)")
        print(f"  Avg Loss: ${stats['avg_loss']:.2f} ({stats['avg_loss_percent']:+.2f}%)")
        print(f"  Profit Factor: {stats['profit_factor']:.2f}")
        
        print(f"\n📊 RISK/REWARD:")
        print(f"  Avg Actual R:R: {stats['avg_rr']:.2f}")
        
        # Grade
        if stats['win_rate'] >= 60 and stats['profit_factor'] >= 2.0:
            grade = "🟢 EXCELLENT"
        elif stats['win_rate'] >= 50 and stats['profit_factor'] >= 1.5:
            grade = "🟡 GOOD"
        elif stats['win_rate'] >= 40 and stats['profit_factor'] >= 1.0:
            grade = "🟠 ACCEPTABLE"
        else:
            grade = "🔴 NEEDS IMPROVEMENT"
        
        print(f"\n🎯 OVERALL GRADE: {grade}")
        print(f"\n{'='*80}\n")
    
    def get_open_positions(self):
        """Get all open positions from database"""
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT ticker, entry_date, entry_price, quantity, stop_loss, target_price,
                   sector, confidence, confluence_score, greek_score
            FROM trades WHERE exit_date IS NULL
            ORDER BY entry_date DESC
        ''')
        
        positions = cursor.fetchall()
        conn.close()
        
        return positions
    
    def display_open_positions(self):
        """Display current open positions"""
        
        positions = self.get_open_positions()
        
        if not positions:
            print("\n💤 No open positions")
            return
        
        # Get current prices from Alpaca
        try:
            alpaca_positions = {p.symbol: float(p.current_price) 
                              for p in self.trading_client.get_all_positions()}
        except:
            alpaca_positions = {}
        
        print(f"\n{'='*80}")
        print(f"📊 OPEN POSITIONS ({len(positions)})")
        print(f"{'='*80}\n")
        
        for pos in positions:
            ticker = pos[0]
            entry_price = pos[2]
            quantity = pos[3]
            stop = pos[4]
            target = pos[5]
            
            current = alpaca_positions.get(ticker, entry_price)
            pnl = (current - entry_price) * quantity
            pnl_pct = ((current - entry_price) / entry_price) * 100
            
            emoji = "🟢" if pnl > 0 else "🔴" if pnl < 0 else "⚪"
            
            print(f"{emoji} {ticker}:")
            print(f"  Entry: ${entry_price:.2f} x {quantity} shares")
            print(f"  Current: ${current:.2f}")
            print(f"  P&L: ${pnl:.2f} ({pnl_pct:+.2f}%)")
            stop_txt = f"${stop:.2f}" if stop is not None else "N/A"
            tgt_txt = f"${target:.2f}" if target is not None else "N/A"
            print(f"  Stop: {stop_txt} | Target: {tgt_txt}")
            if pos[6]:  # sector
                print(f"  Sector: {pos[6]} | Confidence: {pos[7]}")
            print()
        
        print(f"{'='*80}\n")


def main():
    """Main entry point"""
    
    tracker = PerformanceTracker()
    
    print("\n📋 PERFORMANCE TRACKER MENU:")
    print("1. Import current positions from Alpaca")
    print("2. View open positions")
    print("3. View performance report")
    print("4. Log a manual exit")
    print("5. Update trade metadata")
    
    choice = input("\nEnter choice (1-5): ").strip()
    
    if choice == "1":
        tracker.import_current_positions()
        
    elif choice == "2":
        tracker.display_open_positions()
        
    elif choice == "3":
        tracker.display_performance_report()
        
    elif choice == "4":
        ticker = input("Ticker: ").strip().upper()
        exit_price = float(input("Exit price: "))
        reason = input("Exit reason (target/stop/manual): ").strip()
        tracker.log_exit(ticker, exit_price, reason)
        
    elif choice == "5":
        ticker = input("Ticker: ").strip().upper()
        sector = input("Sector (or Enter to skip): ").strip() or None
        confidence = input("Confidence (HIGH/NORMAL, or Enter to skip): ").strip() or None
        tracker.update_trade_metadata(ticker, sector=sector, confidence=confidence)


if __name__ == "__main__":
    main()
