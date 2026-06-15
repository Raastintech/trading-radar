# decision_logger.py
import sqlite3
import json
from datetime import datetime, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo
from decision_contract import normalize_council_decision, normalize_signal

DB_PATH = "trading_performance.db"


class DecisionLogger:
    def __init__(self, db_path=DB_PATH):
        self.db_path = db_path
        self._run_context_cache = {}
        self._ensure_decisions_columns()

    def _ensure_decisions_columns(self):
        """Additive migration for decision analytics columns used by dashboard/reporting."""
        conn = None
        try:
            conn = sqlite3.connect(self.db_path)
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='decisions'")
            if not cur.fetchone():
                conn.close()
                return

            cur.execute("PRAGMA table_info(decisions)")
            existing = {row[1] for row in cur.fetchall()}

            if "strategy" not in existing:
                cur.execute("ALTER TABLE decisions ADD COLUMN strategy TEXT")
            if "direction" not in existing:
                cur.execute("ALTER TABLE decisions ADD COLUMN direction TEXT")
            if "shares" not in existing:
                cur.execute("ALTER TABLE decisions ADD COLUMN shares INTEGER")
            if "execution_denied" not in existing:
                cur.execute("ALTER TABLE decisions ADD COLUMN execution_denied INTEGER DEFAULT 0")
            if "execution_deny_reason" not in existing:
                cur.execute("ALTER TABLE decisions ADD COLUMN execution_deny_reason TEXT")
            if "regime_status" not in existing:
                cur.execute("ALTER TABLE decisions ADD COLUMN regime_status TEXT")
            if "regime_volatility" not in existing:
                cur.execute("ALTER TABLE decisions ADD COLUMN regime_volatility TEXT")
            # Phase 3 enrichment signals
            if "revision_score" not in existing:
                cur.execute("ALTER TABLE decisions ADD COLUMN revision_score REAL")
            if "insider_cluster_score" not in existing:
                cur.execute("ALTER TABLE decisions ADD COLUMN insider_cluster_score REAL")
            if "squeeze_risk_score" not in existing:
                cur.execute("ALTER TABLE decisions ADD COLUMN squeeze_risk_score REAL")
            # Options flow signals
            if "options_pcr" not in existing:
                cur.execute("ALTER TABLE decisions ADD COLUMN options_pcr REAL")
            if "options_gamma" not in existing:
                cur.execute("ALTER TABLE decisions ADD COLUMN options_gamma TEXT")
            if "correlation_blocked" not in existing:
                cur.execute("ALTER TABLE decisions ADD COLUMN correlation_blocked INTEGER DEFAULT 0")
            # Phase 4.3/4.4 pilot telemetry columns
            if "is_pilot" not in existing:
                cur.execute("ALTER TABLE decisions ADD COLUMN is_pilot INTEGER DEFAULT 0")
            if "pilot_policy" not in existing:
                cur.execute("ALTER TABLE decisions ADD COLUMN pilot_policy TEXT")
            if "pilot_threshold" not in existing:
                cur.execute("ALTER TABLE decisions ADD COLUMN pilot_threshold REAL")
            if "pilot_decision_reason" not in existing:
                cur.execute("ALTER TABLE decisions ADD COLUMN pilot_decision_reason TEXT")

            conn.commit()
        except Exception:
            pass
        finally:
            if conn:
                conn.close()

    def _resolve_decisions_table(self, cur):
        cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='decisions'")
        if cur.fetchone():
            return "decisions"
        cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='decisions_old'")
        if cur.fetchone():
            return "decisions_old"
        raise RuntimeError("Missing decisions table (expected 'decisions' or 'decisions_old').")

    def _normalize_regime_status(self, status: str) -> str:
        txt = str(status or "").strip().upper()
        if not txt:
            return ""
        return txt

    def _normalize_regime_volatility(self, volatility: str) -> str:
        txt = str(volatility or "").strip().upper()
        if not txt:
            return ""
        return txt

    def _regime_from_dict(self, regime: dict) -> tuple:
        if not isinstance(regime, dict):
            regime = {}
        status = (
            regime.get("status")
            or regime.get("regime_status")
            or regime.get("spy_regime")
            or regime.get("regime")
        )
        volatility = (
            regime.get("volatility")
            or regime.get("regime_volatility")
        )
        try:
            vix_level = float(regime.get("vix_level"))
        except Exception:
            vix_level = None
        if not volatility and vix_level is not None:
            volatility = "HIGH" if vix_level >= 25 else "NORMAL"
        return self._normalize_regime_status(status), self._normalize_regime_volatility(volatility)

    def _get_run_regime_context(self, run_id: str) -> dict:
        if not run_id:
            return {}
        cached = self._run_context_cache.get(run_id)
        if cached is not None:
            return cached

        conn = None
        try:
            conn = sqlite3.connect(self.db_path)
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='runs'")
            if not cur.fetchone():
                self._run_context_cache[run_id] = {}
                return {}

            cur.execute("PRAGMA table_info(runs)")
            run_cols = {r[1] for r in cur.fetchall()}
            if not {"regime_status", "regime_volatility"} & run_cols:
                self._run_context_cache[run_id] = {}
                return {}

            select_cols = []
            if "regime_status" in run_cols:
                select_cols.append("regime_status")
            if "regime_volatility" in run_cols:
                select_cols.append("regime_volatility")
            cur.execute(f"SELECT {', '.join(select_cols)} FROM runs WHERE run_id=?", (run_id,))
            row = cur.fetchone()
            if not row:
                self._run_context_cache[run_id] = {}
                return {}

            ctx = {}
            idx = 0
            if "regime_status" in run_cols:
                ctx["regime_status"] = self._normalize_regime_status(row[idx])
                idx += 1
            if "regime_volatility" in run_cols:
                ctx["regime_volatility"] = self._normalize_regime_volatility(row[idx])
            self._run_context_cache[run_id] = ctx
            return ctx
        except Exception:
            self._run_context_cache[run_id] = {}
            return {}
        finally:
            if conn:
                conn.close()

    def start_run(self, engine_name="unknown", notes=None, watchlist_size=0,
                  market_session=None, regime_status=None, regime_volatility=None,
                  macro_mode=None, macro_reason=None, next_macro_event=None):
        run_id = str(uuid4())
        ts = datetime.now(timezone.utc).isoformat()
        regime_status = self._normalize_regime_status(regime_status)
        regime_volatility = self._normalize_regime_volatility(regime_volatility)

        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(runs)")
        cols = [r[1] for r in cur.fetchall()]

        payload = {
            "run_id": run_id,
            "timestamp": ts,
            "mode": engine_name.upper(),
            "watchlist_size": int(watchlist_size or 0),
            "market_session": market_session,
            "regime_status": regime_status,
            "regime_volatility": regime_volatility,
            "macro_mode": macro_mode,
            "macro_reason": macro_reason,
            "next_macro_event": next_macro_event,
            "notes": notes or "",
        }

        insert_cols = [c for c in payload.keys() if c in cols]
        insert_vals = [payload[c] for c in insert_cols]

        if not insert_cols:
            conn.close()
            raise RuntimeError(f"runs table schema unexpected. Found columns: {cols}")

        q = f"INSERT INTO runs ({', '.join(insert_cols)}) VALUES ({', '.join(['?']*len(insert_cols))})"
        cur.execute(q, insert_vals)

        conn.commit()
        conn.close()
        self._run_context_cache[run_id] = {
            "regime_status": regime_status,
            "regime_volatility": regime_volatility,
        }
        return run_id

    def finalize_run(self, run_id: str):
        """
        Institutional safety: ensures runs.watchlist_size matches actual logged decisions.
        This prevents dashboard corruption from caller mistakes.
        """
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()

        cur.execute("PRAGMA table_info(runs)")
        cols = {r[1] for r in cur.fetchall()}
        if "watchlist_size" not in cols:
            conn.close()
            return

        cur.execute("SELECT COUNT(*) FROM decisions WHERE run_id=?", (run_id,))
        n = cur.fetchone()[0]

        cur.execute("UPDATE runs SET watchlist_size=? WHERE run_id=?", (n, run_id))
        conn.commit()
        conn.close()

    def log_decision(
        self,
        run_id: str,
        ticker: str,
        council_decision: dict,
        signal: dict = None,
        market_session: str = None,
        sentiment: dict = None,
        regime: dict = None,
        macro: dict = None,
        notes: str = None,
        setup_type: str = None,
        order_submitted: int = 0,
        order_id: str = None,
        position_opened: int = 0,
        linked_trade_id: int = None,
        execution_denied: int = 0,
        execution_deny_reason: str = None,
        revision_score: float = None,
        insider_cluster_score: float = None,
        squeeze_risk_score: float = None,
        options_pcr: float = None,
        options_gamma: str = None,
        correlation_blocked: int = None,
        is_pilot: int = None,
        pilot_policy: str = None,
        pilot_threshold: float = None,
        pilot_decision_reason: str = None,
    ):
        """
        Writes one row into decisions table using your schema.
        Returns the inserted decision row id when available.
        """
        signal = normalize_signal(signal or {})
        sentiment = sentiment or {}
        regime = regime or {}
        macro = macro or {}
        council_decision = normalize_council_decision(council_decision or {})

        regime_status, regime_volatility = self._regime_from_dict(regime)
        if not regime_status or not regime_volatility:
            run_ctx = self._get_run_regime_context(run_id)
            if not regime_status:
                regime_status = run_ctx.get("regime_status", "")
            if not regime_volatility:
                regime_volatility = run_ctx.get("regime_volatility", "")

        # Never allow blank market_session to be written
        if market_session is None or str(market_session).strip() == "":
            market_session = "CLOSED"

        ts = datetime.now().isoformat()
        ticker = ticker.upper()

        # Robust RR extraction
        rr = (
            signal.get("risk_reward")
            or signal.get("risk_reward_ratio")
            or signal.get("risk_reward_r")
            or signal.get("rr")
        )

        if market_session is None:
            market_session = self._market_session_now()

        raw_votes = council_decision.get("raw_votes")
        if raw_votes is None:
            raw_votes = council_decision.get("votes") or council_decision.get("agent_votes") or {}
        if not isinstance(raw_votes, dict):
            raw_votes = {}
        votes_json = json.dumps(raw_votes, default=str)

        regime_filter_details = ((raw_votes.get("regime_filter") or {}).get("details") or {}) if isinstance(raw_votes, dict) else {}
        execution_details = ((raw_votes.get("execution_guard") or {}).get("details") or {}) if isinstance(raw_votes, dict) else {}
        execution_session = (execution_details.get("session") or {}).get("session") if isinstance(execution_details, dict) else None
        execution_volume = (execution_details.get("liquidity") or {}).get("volume") if isinstance(execution_details, dict) else None

        # Runtime scanner paths often have empty raw_votes; in that case we still
        # persist regime_overall/regime_vix from the run-level regime context.
        regime_overall = self._normalize_regime_status(
            regime_filter_details.get("overall_regime")
            or (regime.get("regime_overall") if isinstance(regime, dict) else None)
            or (regime.get("overall_regime") if isinstance(regime, dict) else None)
            or regime_status
        )
        regime_spy = self._normalize_regime_status(
            regime_filter_details.get("spy_regime")
            or (regime.get("regime_spy") if isinstance(regime, dict) else None)
            or (regime.get("spy_regime") if isinstance(regime, dict) else None)
            or regime_status
        )
        regime_vix = self._normalize_regime_volatility(
            regime_filter_details.get("vix_regime")
            or (regime.get("regime_vix") if isinstance(regime, dict) else None)
            or (regime.get("vix_regime") if isinstance(regime, dict) else None)
            or regime_volatility
        )

        row = {
            "run_id": run_id,
            "timestamp": ts,
            "ticker": ticker,
            "strategy": (
                (council_decision or {}).get("strategy")
                or (signal or {}).get("strategy")
                or setup_type
                or (signal or {}).get("signal")
                or "UNKNOWN"
            ),
            "direction": (
                (signal or {}).get("direction")
                or (council_decision or {}).get("direction")
            ),
            "shares": (
                (council_decision or {}).get("shares")
                or (signal or {}).get("shares")
            ),

            "market_session": market_session,

            "signal": signal.get("signal"),
            "confluence_score": signal.get("composite_score"),

            "entry_price": signal.get("entry_price"),
            "stop_loss": signal.get("stop_loss"),
            "target_price": signal.get("target_price"),
            "rr": rr,

            "council_decision": council_decision.get("decision"),
            "council_reason": council_decision.get("reason"),
            "approve_count": council_decision.get("approve_count"),
            "caution_count": council_decision.get("caution_count"),
            "veto_count": council_decision.get("veto_count"),
            "avg_score": council_decision.get("avg_score"),

            "veto_reasons_json": json.dumps(council_decision.get("veto_reasons", [])),
            "votes_json": votes_json,

            "sentiment_score": sentiment.get("sentiment_score"),
            "sentiment_conf": sentiment.get("confidence"),
            "sentiment_label": sentiment.get("label"),

            "regime_status": regime_status or None,
            "regime_volatility": regime_volatility or None,

            "macro_event_name": macro.get("event_name"),
            "macro_impact": macro.get("impact"),
            "macro_minutes_to_event": macro.get("minutes_to_event"),

            # piggybacked macro context via regime dict
            "macro_mode": (regime.get("macro_mode") if isinstance(regime, dict) else None),
            "macro_next_event": (regime.get("macro_next_event") if isinstance(regime, dict) else None),
            "macro_reason": (regime.get("macro_reason") if isinstance(regime, dict) else None),

            "order_submitted": int(order_submitted),
            "order_id": order_id,
            "position_opened": int(position_opened),
            "execution_denied": int(execution_denied or 0),
            "execution_deny_reason": execution_deny_reason,

            "notes": notes,
            "setup_type": setup_type,
            "linked_trade_id": linked_trade_id,

            # regime/execution context for richer analytics
            "regime_multiplier": council_decision.get("regime_multiplier"),
            "regime_overall": regime_overall or None,
            "regime_spy": regime_spy or None,
            "regime_vix": regime_vix or None,
            "execution_session": execution_session,
            "execution_volume": execution_volume,

            # all fresh decisions start pending; nightly auditor fills outcome
            "audit_status": "PENDING",
            "audit_outcome": None,
            "audit_updated_at": None,

            # Phase 3 enrichment signals
            "revision_score":        revision_score,
            "insider_cluster_score": insider_cluster_score,
            "squeeze_risk_score":    squeeze_risk_score,
            # Options flow signals
            "options_pcr":           options_pcr,
            "options_gamma":         options_gamma,
            "correlation_blocked":   correlation_blocked,
            # Pilot telemetry
            "is_pilot":              is_pilot,
            "pilot_policy":          pilot_policy,
            "pilot_threshold":       pilot_threshold,
            "pilot_decision_reason": pilot_decision_reason,
        }

        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()

        decisions_table = self._resolve_decisions_table(cur)
        cur.execute(f"PRAGMA table_info({decisions_table})")
        decision_cols = {r[1] for r in cur.fetchall()}

        cols = [k for k, v in row.items() if v is not None and k in decision_cols]
        if "votes_json" in decision_cols and "votes_json" not in cols:
            cols.append("votes_json")
        vals = [row[k] for k in cols]

        if not cols:
            conn.close()
            raise RuntimeError(
                f"No compatible columns found for {decisions_table}. "
                f"Row keys={list(row.keys())}, table_cols={sorted(decision_cols)}"
            )

        q = f"INSERT INTO {decisions_table} ({', '.join(cols)}) VALUES ({', '.join(['?']*len(cols))})"
        cur.execute(q, vals)
        decision_id = cur.lastrowid

        conn.commit()
        conn.close()
        return decision_id

    def _market_session_now(self) -> str:
        now_et = datetime.now(ZoneInfo("America/New_York"))
        if now_et.weekday() >= 5:
            return "CLOSED"
        mins = now_et.hour * 60 + now_et.minute
        if 4 * 60 <= mins < 9 * 60 + 30:
            return "PRE"
        if 9 * 60 + 30 <= mins < 16 * 60:
            return "REGULAR"
        if 16 * 60 <= mins < 20 * 60:
            return "POST"
        return "CLOSED"
