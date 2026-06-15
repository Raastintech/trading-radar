"""
shadow_outcome_tracker.py — Phase 4.2 Shadow Outcome Tracker

READ-ONLY ANALYSIS. Zero execution side effects.

Tracks "would-have-passed" candidates under shadow RR thresholds.
Records their forward price outcomes without any trade placement.
"""
import json
import logging
import os
import sqlite3
from datetime import datetime, timezone, timedelta
from typing import Dict, Iterable, List, Optional, Tuple, Union

logger = logging.getLogger(__name__)

DB_PATH = "trading_performance.db"

# Shadow policies: strategy + threshold that would have passed
SHADOW_POLICIES: Dict[str, Dict] = {
    "short_3_0": {"strategy": "SHORT",  "shadow_threshold": 3.0, "live_threshold": 3.0, "direction": "SHORT"},
    "short_2_5": {"strategy": "SHORT",  "shadow_threshold": 2.5, "live_threshold": 3.0, "direction": "SHORT"},
    "sniper_2_2": {"strategy": "SNIPER", "shadow_threshold": 2.2, "live_threshold": 2.5, "direction": "LONG"},
    "sniper_2_0": {"strategy": "SNIPER", "shadow_threshold": 2.0, "live_threshold": 2.5, "direction": "LONG"},
    # Phase 5.1: SHORT with tighter stop geometry — paper calibration only.
    # The 4.0 policy is now a stricter-than-live calibration branch.
    "short_tight_4_0": {
        "strategy": "SHORT",
        "shadow_threshold": 4.0,
        "live_threshold": 3.0,
        "direction": "SHORT",
        "requires_stop_mult": 1.04,
    },
    "short_tight_3_0": {
        "strategy": "SHORT",
        "shadow_threshold": 3.0,
        "live_threshold": 3.0,
        "direction": "SHORT",
        "requires_stop_mult": 1.04,
    },
}

# No data for price fetch → mark NO_DATA. Never blocks workflow.
_OUTCOME_STATUS_PENDING  = "PENDING"
_OUTCOME_STATUS_COMPUTED = "COMPUTED"
_OUTCOME_STATUS_NO_DATA  = "NO_DATA"

_TRACKING_MODE = "PAPER"  # always; never executes real orders
DEFAULT_TRACKING_HORIZONS: Tuple[int, ...] = (5, 20)
DEFAULT_SUMMARY_HORIZON: int = 5


class ShadowOutcomeTracker:
    """
    Seeds shadow candidates from the decisions table and computes forward
    price outcomes. Purely analytical — no order submission.
    """

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._alpaca_feed = None
        self._price_bar_cache: Dict[Tuple[str, str, int], List[dict]] = {}
        self._ensure_tables()

    # ── Public API ───────────────────────────────────────────────────────────

    def seed_candidates(
        self,
        days: int = 7,
        policies: Optional[List[str]] = None,
        horizon_days: Optional[Union[int, Iterable[int]]] = None,
    ) -> int:
        """
        Scan decisions table for RR rejects that qualify under shadow policies.
        Insert qualifying rows into shadow_candidates (skip duplicates).

        Returns: count of newly inserted candidates.
        """
        if policies is None:
            policies = list(SHADOW_POLICIES.keys())

        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        inserted = 0
        horizons = self._normalize_horizons(
            horizon_days,
            default=DEFAULT_TRACKING_HORIZONS,
        )

        for policy_name in policies:
            policy = SHADOW_POLICIES.get(policy_name)
            if not policy:
                logger.warning(f"Unknown policy: {policy_name}")
                continue

            strat = policy["strategy"]
            shadow_thr = policy["shadow_threshold"]
            live_thr = policy["live_threshold"]
            direction = policy["direction"]

            try:
                rows = self._fetch_qualifying_rejects(
                    cutoff=cutoff,
                    strategy=strat,
                    shadow_threshold=shadow_thr,
                )
                for row in rows:
                    for horizon in horizons:
                        ok = self._insert_candidate(
                            run_id=row.get("run_id"),
                            ticker=row["ticker"],
                            strategy=strat,
                            policy_name=policy_name,
                            shadow_threshold=shadow_thr,
                            live_threshold=live_thr,
                            observed_rr=row.get("rr"),
                            entry_price=row.get("entry_price"),
                            stop_price=row.get("stop_loss"),
                            target_price=row.get("target_price"),
                            direction=direction,
                            reject_timestamp=row.get("timestamp"),
                            horizon_days=horizon,
                        )
                        if ok:
                            inserted += 1
            except Exception as e:
                logger.error(f"seed_candidates policy={policy_name}: {e}")

        logger.info(f"Shadow seeder: {inserted} new candidates across {len(policies)} policies")
        return inserted

    def compute_outcomes(
        self,
        horizon_days: Optional[Union[int, Iterable[int]]] = None,
        retry_no_data: bool = True,
    ) -> int:
        """
        Resolve elapsed PAPER candidates into shadow_outcomes.

        If horizon_days is None, resolve using each candidate's stored horizon.
        Candidates with insufficient elapsed trading bars remain PENDING.
        Candidates previously marked NO_DATA are retried when retry_no_data=True.

        Returns: count of outcomes computed.
        """
        horizons = self._normalize_horizons(horizon_days, default=None)
        if horizons is None:
            pending = self._fetch_pending_candidates(
                horizon_days=None,
                retry_no_data=retry_no_data,
            )
        else:
            pending = []
            for horizon in horizons:
                pending.extend(
                    self._fetch_pending_candidates(
                        horizon_days=horizon,
                        retry_no_data=retry_no_data,
                    )
                )
        computed = 0

        for cand in pending:
            try:
                required_horizon = int(cand.get("horizon_days") or horizon_days or 5)
                bars, data_source = self._fetch_price_bars_with_source(
                    ticker=cand["ticker"],
                    from_date=cand["reject_timestamp"][:10],
                    n_days=required_horizon + 5,  # buffer for weekends
                )
                if not bars:
                    if self._should_mark_no_data(
                        reject_timestamp=cand["reject_timestamp"],
                        horizon_days=required_horizon,
                    ):
                        self._mark_no_data(cand["id"])
                    continue

                # Use first bar after reject date as entry reference
                entry_px = cand["entry_price"] or (bars[0]["close"] if bars else None)
                if not entry_px:
                    if self._should_mark_no_data(
                        reject_timestamp=cand["reject_timestamp"],
                        horizon_days=required_horizon,
                    ):
                        self._mark_no_data(cand["id"])
                    continue

                # Do not finalize until the full trading-day horizon exists.
                if len(bars) < required_horizon:
                    continue
                bars = bars[:required_horizon]

                exit_px = bars[-1]["close"]
                direction = cand["direction"]
                stop_px = cand["stop_price"]
                target_px = cand["target_price"]

                outcome = self._compute_outcome(
                    bars=bars,
                    entry_price=entry_px,
                    stop_price=stop_px,
                    target_price=target_px,
                    direction=direction,
                    exit_price=exit_px,
                )
                outcome["data_source"] = data_source or "unknown"

                self._insert_outcome(
                    cand_id=cand["id"],
                    entry_price=entry_px,
                    outcome=outcome,
                    horizon_days=required_horizon,
                )
                self._update_candidate_status(cand["id"], _OUTCOME_STATUS_COMPUTED)
                computed += 1

            except Exception as e:
                logger.warning(f"compute_outcomes {cand.get('ticker')}: {e}")

        logger.info(f"Shadow outcomes: {computed} computed from {len(pending)} pending")
        return computed

    def get_summary(
        self,
        policies: Optional[List[str]] = None,
        horizon_days: Optional[int] = DEFAULT_SUMMARY_HORIZON,
    ) -> dict:
        """Return aggregate outcome summary by policy for a single horizon."""
        if policies is None:
            policies = list(SHADOW_POLICIES.keys())

        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            placeholders = ",".join("?" * len(policies))
            params: List[object] = list(policies)
            horizon_clause = ""
            if horizon_days is not None:
                horizon_clause = " AND sc.horizon_days = ?"
                params.append(int(horizon_days))

            cur.execute(
                f"""
                SELECT
                    sc.policy_name,
                    sc.strategy,
                    COUNT(sc.id) as total_candidates,
                    SUM(CASE WHEN sc.outcome_status='COMPUTED' THEN 1 ELSE 0 END) as completed,
                    SUM(CASE WHEN so.hit_target=1 THEN 1 ELSE 0 END) as hits_target,
                    SUM(CASE WHEN so.hit_stop=1 THEN 1 ELSE 0 END) as hits_stop,
                    AVG(so.end_return_pct) as avg_return,
                    AVG(so.max_favorable_excursion) as avg_mfe,
                    AVG(so.max_adverse_excursion) as avg_mae
                FROM shadow_candidates sc
                LEFT JOIN shadow_outcomes so ON so.candidate_id = sc.id
                WHERE sc.policy_name IN ({placeholders})
                  {horizon_clause}
                GROUP BY sc.policy_name, sc.strategy
                """,
                params,
            )
            rows = cur.fetchall()
            conn.close()

            result = {}
            for row in rows:
                policy_name = row["policy_name"]
                completed = row["completed"] or 0
                hits_target = row["hits_target"] or 0
                hits_stop = row["hits_stop"] or 0
                result[policy_name] = {
                    "strategy": row["strategy"],
                    "total_candidates": row["total_candidates"],
                    "completed_outcomes": completed,
                    "target_hit_pct": round(hits_target / completed * 100, 1) if completed else None,
                    "stop_hit_pct": round(hits_stop / completed * 100, 1) if completed else None,
                    "avg_return_pct": round(row["avg_return"], 2) if row["avg_return"] is not None else None,
                    "avg_mfe_pct": round(row["avg_mfe"], 2) if row["avg_mfe"] is not None else None,
                    "avg_mae_pct": round(row["avg_mae"], 2) if row["avg_mae"] is not None else None,
                    "horizon_days": int(horizon_days) if horizon_days is not None else None,
                }
            return result
        except Exception as e:
            logger.error(f"get_summary error: {e}")
            return {}

    def get_multi_horizon_summary(
        self,
        policies: Optional[List[str]] = None,
        horizon_days: Optional[Union[int, Iterable[int]]] = None,
    ) -> Dict[str, dict]:
        """Return summaries keyed by horizon label, preserving single-horizon semantics."""
        horizons = self._normalize_horizons(
            horizon_days,
            default=DEFAULT_TRACKING_HORIZONS,
        )
        result: Dict[str, dict] = {}
        for horizon in horizons:
            result[f"horizon_{int(horizon)}d"] = self.get_summary(
                policies=policies,
                horizon_days=int(horizon),
            )
        return result

    def fetch_price_bars(
        self,
        ticker: str,
        from_date: str,
        n_days: int,
    ) -> Tuple[List[dict], str]:
        """
        Public read-only price path helper for research tools.

        Returns:
            (bars, source)

        This intentionally reuses the same Alpaca/yfinance fallback path used by
        the shadow outcome tracker so research tools evaluate candidates against
        the same market data stack.
        """
        return self._fetch_price_bars_with_source(ticker=ticker, from_date=from_date, n_days=n_days)

    def print_summary(self, summary: dict) -> None:
        sep = "─" * 72
        print(f"\n{'═'*72}")
        print(f"  SHADOW OUTCOME TRACKER  [PAPER/ANALYSIS ONLY — NO LIVE TRADES]")
        print(f"{'═'*72}")
        if not summary:
            print(f"  No shadow outcome data yet.")
            print(f"{'═'*72}\n")
            return
        print(f"  {'Policy':<14}  {'Cands':>6}  {'Done':>5}  {'Tgt%':>6}  {'Stp%':>6}  {'AvgRet':>8}  {'AvgMFE':>8}  {'AvgMAE':>8}")
        print(f"  {sep}")
        for policy, data in sorted(summary.items()):
            n = data["total_candidates"]
            done = data["completed_outcomes"]
            tgt = f"{data['target_hit_pct']:.0f}%" if data["target_hit_pct"] is not None else "  —"
            stp = f"{data['stop_hit_pct']:.0f}%" if data["stop_hit_pct"] is not None else "  —"
            ret = f"{data['avg_return_pct']:+.1f}%" if data["avg_return_pct"] is not None else "    —"
            mfe = f"{data['avg_mfe_pct']:.1f}%" if data["avg_mfe_pct"] is not None else "   —"
            mae = f"{data['avg_mae_pct']:.1f}%" if data["avg_mae_pct"] is not None else "   —"
            print(f"  {policy:<14}  {n:>6}  {done:>5}  {tgt:>6}  {stp:>6}  {ret:>8}  {mfe:>8}  {mae:>8}")
        print(f"\n  tracking_mode=PAPER  No orders submitted.\n{'═'*72}\n")

    def print_multi_horizon_summary(self, summary_by_horizon: Dict[str, dict]) -> None:
        for label, summary in sorted(summary_by_horizon.items()):
            horizon_label = label.replace("horizon_", "").replace("d", "d horizon")
            print(f"\n### {horizon_label.upper()}")
            self.print_summary(summary)

    # ── Internal: data access ────────────────────────────────────────────────

    def _fetch_qualifying_rejects(
        self,
        cutoff: str,
        strategy: str,
        shadow_threshold: float,
    ) -> List[dict]:
        """Fetch decisions rows for RR rejects >= shadow_threshold for strategy."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        cur.execute("PRAGMA table_info(decisions)")
        dcols = {r["name"] for r in cur.fetchall()}

        # Need: ticker, rr, timestamp, run_id, entry_price, stop_loss, target_price
        needed = {"ticker", "rr", "timestamp", "execution_deny_reason", "strategy"}
        if not needed.issubset(dcols):
            conn.close()
            return []

        sel_entry = "entry_price" if "entry_price" in dcols else "NULL as entry_price"
        sel_stop  = "stop_loss"   if "stop_loss"   in dcols else "NULL as stop_loss"
        sel_target= "target_price"if "target_price" in dcols else "NULL as target_price"
        sel_run   = "run_id"      if "run_id"       in dcols else "NULL as run_id"

        cur.execute(
            f"""
            SELECT ticker, rr, timestamp, {sel_run}, {sel_entry}, {sel_stop}, {sel_target}
            FROM decisions
            WHERE timestamp >= ?
              AND UPPER(TRIM(strategy)) = ?
              AND execution_deny_reason = 'risk_reward_too_low'
              AND rr IS NOT NULL
              AND rr >= ?
            ORDER BY timestamp DESC
            """,
            (cutoff, strategy.upper(), shadow_threshold),
        )
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows

    def _insert_candidate(self, **kwargs) -> bool:
        """Insert a shadow candidate. Returns True if newly inserted."""
        run_id = kwargs.get("run_id") or ""
        ticker = kwargs["ticker"]
        policy_name = kwargs["policy_name"]
        horizon_days = kwargs.get("horizon_days", 5)

        try:
            conn = sqlite3.connect(self.db_path)
            cur = conn.cursor()
            cur.execute(
                """
                INSERT OR IGNORE INTO shadow_candidates
                    (created_at, seeded_at, run_id, ticker, strategy, policy_name,
                     shadow_threshold, live_threshold, observed_rr,
                     entry_price, stop_price, target_price, direction,
                     reject_timestamp, horizon_days, outcome_status, tracking_mode)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    datetime.now(timezone.utc).isoformat(),
                    datetime.now(timezone.utc).isoformat(),
                    run_id,
                    ticker,
                    kwargs["strategy"],
                    policy_name,
                    kwargs["shadow_threshold"],
                    kwargs["live_threshold"],
                    kwargs.get("observed_rr"),
                    kwargs.get("entry_price"),
                    kwargs.get("stop_price"),
                    kwargs.get("target_price"),
                    kwargs.get("direction", "LONG"),
                    kwargs.get("reject_timestamp") or datetime.now(timezone.utc).isoformat(),
                    horizon_days,
                    _OUTCOME_STATUS_PENDING,
                    _TRACKING_MODE,
                ),
            )
            inserted = cur.rowcount > 0
            conn.commit()
            conn.close()
            return inserted
        except Exception as e:
            logger.debug(f"_insert_candidate {ticker}/{policy_name}: {e}")
            return False

    def _fetch_pending_candidates(
        self,
        horizon_days: Optional[int],
        retry_no_data: bool = True,
    ) -> List[dict]:
        """Return PAPER candidates eligible for outcome resolution attempts."""
        statuses = ["PENDING"]
        if retry_no_data:
            statuses.append("NO_DATA")
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            params: List[object] = list(statuses)
            sql = f"""
                SELECT id, ticker, strategy, policy_name, direction,
                       entry_price, stop_price, target_price,
                       reject_timestamp, horizon_days
                FROM shadow_candidates
                WHERE outcome_status IN ({",".join("?" * len(statuses))})
                  AND tracking_mode = 'PAPER'
            """
            if horizon_days is not None:
                sql += " AND horizon_days = ?"
                params.append(int(horizon_days))
            sql += " ORDER BY reject_timestamp ASC, id ASC"
            cur.execute(sql, params)
            rows = [dict(r) for r in cur.fetchall()]
            conn.close()
            return rows
        except Exception as e:
            logger.error(f"_fetch_pending_candidates: {e}")
            return []

    @staticmethod
    def _normalize_horizons(
        horizon_days: Optional[Union[int, Iterable[int]]],
        default: Optional[Iterable[int]],
    ) -> Optional[List[int]]:
        if horizon_days is None:
            if default is None:
                return None
            return [int(h) for h in default]
        if isinstance(horizon_days, int):
            return [int(horizon_days)]
        horizons: List[int] = []
        for value in horizon_days:
            try:
                horizon = int(value)
            except (TypeError, ValueError):
                continue
            if horizon not in horizons:
                horizons.append(horizon)
        return horizons or ([int(h) for h in default] if default is not None else None)

    def _fetch_price_bars(self, ticker: str, from_date: str, n_days: int) -> List[dict]:
        """Fetch OHLC bars starting from from_date. Alpaca-first, Yahoo fallback."""
        bars, _ = self._fetch_price_bars_with_source(ticker=ticker, from_date=from_date, n_days=n_days)
        return bars

    def _fetch_price_bars_with_source(self, ticker: str, from_date: str, n_days: int) -> Tuple[List[dict], str]:
        """Fetch OHLC bars and return both the bars and the source label."""
        cache_key = (str(ticker or "").upper().strip(), str(from_date or "")[:10], int(n_days or 0))
        cached = self._price_bar_cache.get(cache_key)
        if cached is not None:
            return list(cached), "cache"

        bars = self._fetch_price_bars_alpaca(ticker=ticker, from_date=from_date, n_days=n_days)
        if not bars:
            bars = self._fetch_price_bars_yfinance(ticker=ticker, from_date=from_date, n_days=n_days)
            source = "yfinance" if bars else "unknown"
        else:
            source = "alpaca"
        self._price_bar_cache[cache_key] = list(bars)
        return list(bars), source

    def _fetch_price_bars_alpaca(self, ticker: str, from_date: str, n_days: int) -> List[dict]:
        """Fetch OHLC bars using the system Alpaca data feed."""
        try:
            from alpaca_data import AlpacaDataFeed
        except Exception:
            return []

        try:
            if self._alpaca_feed is None:
                self._alpaca_feed = AlpacaDataFeed()

            start = datetime.strptime(from_date[:10], "%Y-%m-%d").date()
            today = datetime.now(timezone.utc).date()
            days_back = max((today - start).days + 5, int(n_days or 0) + 5, 10)
            raw_bars = self._alpaca_feed.get_daily_bars(ticker, days_back=days_back, adjustment="all")
            bars = []
            for bar in raw_bars or []:
                ts = bar.get("timestamp")
                bar_date = None
                if hasattr(ts, "date"):
                    bar_date = ts.date()
                else:
                    try:
                        bar_date = datetime.fromisoformat(str(ts).replace("Z", "+00:00")).date()
                    except Exception:
                        bar_date = None
                if bar_date is None or bar_date < start:
                    continue
                bars.append({
                    "date": bar_date.isoformat(),
                    "open": float(bar["open"]),
                    "high": float(bar["high"]),
                    "low": float(bar["low"]),
                    "close": float(bar["close"]),
                    "volume": int(bar.get("volume", 0) or 0),
                })
            bars.sort(key=lambda b: b["date"])
            return bars
        except Exception as e:
            logger.debug(f"_fetch_price_bars_alpaca {ticker}: {e}")
            return []

    def _fetch_price_bars_yfinance(self, ticker: str, from_date: str, n_days: int) -> List[dict]:
        """Fetch OHLC bars using yfinance as a fallback source."""
        try:
            import yfinance as yf
            start = datetime.strptime(from_date[:10], "%Y-%m-%d").date()
            end = start + timedelta(days=n_days + 10)
            df = yf.download(ticker, start=str(start), end=str(end), progress=False, auto_adjust=True)
            if df is None or df.empty:
                return []
            bars = []
            for idx, row in df.iterrows():
                bars.append({
                    "date": str(idx)[:10],
                    "open":  float(row["Open"]),
                    "high":  float(row["High"]),
                    "low":   float(row["Low"]),
                    "close": float(row["Close"]),
                    "volume": int(row.get("Volume", 0) or 0),
                })
            bars.sort(key=lambda b: b["date"])
            return bars
        except ImportError:
            logger.debug("yfinance not available for shadow outcomes")
            return []
        except Exception as e:
            logger.debug(f"_fetch_price_bars_yfinance {ticker}: {e}")
            return []

    def _should_mark_no_data(self, reject_timestamp: str, horizon_days: int) -> bool:
        """
        Avoid finalizing NO_DATA before the requested outcome horizon has had
        reasonable time to materialize. This keeps March 18 5-day cohorts from
        being marked missing on March 23 before the fifth trading day exists.
        """
        try:
            normalized = str(reject_timestamp or "").replace("Z", "+00:00")
            reject_dt = datetime.fromisoformat(normalized)
            if reject_dt.tzinfo is None:
                reject_dt = reject_dt.replace(tzinfo=timezone.utc)
        except Exception:
            return False
        age_days = max(0, (datetime.now(timezone.utc) - reject_dt.astimezone(timezone.utc)).days)
        return age_days >= max(int(horizon_days or 0) + 7, 10)

    def _compute_outcome(
        self,
        bars: List[dict],
        entry_price: float,
        stop_price: Optional[float],
        target_price: Optional[float],
        direction: str,
        exit_price: float,
    ) -> dict:
        """Compute outcome metrics from OHLC bars."""
        is_short = direction.upper() == "SHORT"

        mfe = 0.0
        mae = 0.0
        hit_target = False
        hit_stop = False

        for bar in bars:
            high = bar["high"]
            low  = bar["low"]

            if is_short:
                # favorable = price goes down
                favorable_excursion = (entry_price - low) / entry_price * 100
                adverse_excursion   = (high - entry_price) / entry_price * 100
                if target_price and low <= target_price:
                    hit_target = True
                if stop_price and high >= stop_price:
                    hit_stop = True
            else:
                favorable_excursion = (high - entry_price) / entry_price * 100
                adverse_excursion   = (entry_price - low)  / entry_price * 100
                if target_price and high >= target_price:
                    hit_target = True
                if stop_price and low <= stop_price:
                    hit_stop = True

            mfe = max(mfe, favorable_excursion)
            mae = max(mae, adverse_excursion)

        if is_short:
            end_return = (entry_price - exit_price) / entry_price * 100
        else:
            end_return = (exit_price - entry_price) / entry_price * 100

        return {
            "end_return_pct": round(end_return, 3),
            "max_favorable_excursion": round(mfe, 3),
            "max_adverse_excursion": round(mae, 3),
            "hit_target": int(hit_target),
            "hit_stop": int(hit_stop),
            "exit_price": round(exit_price, 4),
            "data_available": 1,
            "data_source": "unknown",
        }

    def _insert_outcome(self, cand_id: int, entry_price: float, outcome: dict, horizon_days: int) -> None:
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute("DELETE FROM shadow_outcomes WHERE candidate_id = ?", (cand_id,))
            conn.execute(
                """
                INSERT INTO shadow_outcomes
                    (candidate_id, computed_at, horizon_days, entry_price, exit_price,
                     end_return_pct, max_favorable_excursion, max_adverse_excursion,
                     hit_target, hit_stop, data_source, data_available, tracking_mode)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    cand_id,
                    datetime.now(timezone.utc).isoformat(),
                    horizon_days,
                    float(entry_price),
                    outcome.get("exit_price"),
                    outcome.get("end_return_pct"),
                    outcome.get("max_favorable_excursion"),
                    outcome.get("max_adverse_excursion"),
                    outcome.get("hit_target", 0),
                    outcome.get("hit_stop", 0),
                    outcome.get("data_source", "unknown"),
                    outcome.get("data_available", 1),
                    _TRACKING_MODE,
                ),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"_insert_outcome candidate_id={cand_id}: {e}")

    def _update_candidate_status(self, cand_id: int, status: str) -> None:
        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                "UPDATE shadow_candidates SET outcome_status=? WHERE id=?",
                (status, cand_id),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"_update_candidate_status: {e}")

    def _mark_no_data(self, cand_id: int) -> None:
        self._update_candidate_status(cand_id, _OUTCOME_STATUS_NO_DATA)

    # ── Schema migration ─────────────────────────────────────────────────────

    def _ensure_tables(self) -> None:
        try:
            conn = sqlite3.connect(self.db_path)
            cur = conn.cursor()

            cur.execute("""
                CREATE TABLE IF NOT EXISTS shadow_candidates (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at       TEXT NOT NULL,
                    seeded_at        TEXT NOT NULL,
                    run_id           TEXT,
                    ticker           TEXT NOT NULL,
                    strategy         TEXT NOT NULL,
                    policy_name      TEXT NOT NULL,
                    shadow_threshold REAL NOT NULL,
                    live_threshold   REAL NOT NULL,
                    observed_rr      REAL,
                    entry_price      REAL,
                    stop_price       REAL,
                    target_price     REAL,
                    direction        TEXT NOT NULL DEFAULT 'LONG',
                    reject_timestamp TEXT NOT NULL,
                    horizon_days     INTEGER NOT NULL DEFAULT 5,
                    outcome_status   TEXT NOT NULL DEFAULT 'PENDING',
                    tracking_mode    TEXT NOT NULL DEFAULT 'PAPER'
                )
            """)

            cur.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS uq_shadow_candidates
                ON shadow_candidates(run_id, ticker, policy_name, horizon_days)
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS shadow_outcomes (
                    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
                    candidate_id             INTEGER NOT NULL,
                    computed_at              TEXT NOT NULL,
                    horizon_days             INTEGER NOT NULL,
                    entry_price              REAL,
                    exit_price               REAL,
                    end_return_pct           REAL,
                    max_favorable_excursion  REAL,
                    max_adverse_excursion    REAL,
                    hit_target               INTEGER DEFAULT 0,
                    hit_stop                 INTEGER DEFAULT 0,
                    data_source              TEXT,
                    data_available           INTEGER DEFAULT 1,
                    tracking_mode            TEXT NOT NULL DEFAULT 'PAPER'
                )
            """)

            cur.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS uq_shadow_outcomes_candidate
                ON shadow_outcomes(candidate_id)
            """)

            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"_ensure_tables shadow: {e}")
