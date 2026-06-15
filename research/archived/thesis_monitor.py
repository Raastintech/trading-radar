"""
Thesis Monitor — Open Position Lifecycle Health Tracking

States:  INTACT → WARN → BROKEN
Strategy-specific checks:
  SNIPER  : MA crossover, volume trend, momentum reversal
  VOYAGER : MA trend, consecutive closes above entry
  REMORA  : Correlated instrument flow, short-term momentum
  DEFAULT : Generic score drift + price action

Usage:
    from thesis_monitor import ThesisMonitor
    monitor = ThesisMonitor(data_feed)

    result = monitor.check_thesis(ticker, strategy, entry_price, scores)
    # result = {"status": "INTACT", "strength": 0.82, "reason": "...", "checks": {...}}

    # Bulk: pass list of open position dicts
    updates = monitor.check_all_positions(positions)
"""

import sqlite3
import os
from datetime import datetime, timezone
from typing import Optional


DB_PATH = os.environ.get("TRADING_DB_PATH", "trading_performance.db")

# Status constants
INTACT  = "INTACT"
WARN    = "WARN"
BROKEN  = "BROKEN"
NONE_ST = "NONE"


class ThesisMonitor:
    """
    Per-position thesis health evaluator.

    Requires an AlpacaDataFeed instance for price/bar data.
    """

    def __init__(self, data_feed=None):
        self._feed = data_feed

    # -----------------------------------------------------------------------
    # public API
    # -----------------------------------------------------------------------

    def check_thesis(
        self,
        ticker: str,
        strategy: str,
        entry_price: float,
        scores: Optional[dict] = None,
        stop_loss: Optional[float] = None,
        target_price: Optional[float] = None,
    ) -> dict:
        """
        Evaluate thesis health for one open position.

        Returns:
            {
                "status":   "INTACT" | "WARN" | "BROKEN",
                "strength": float,   # 0.0-1.0
                "reason":   str,
                "checks":   dict,    # per-signal results
            }
        """
        scores = scores or {}
        strategy = (strategy or "").upper().strip()

        try:
            if strategy in ("SNIPER", "SNP"):
                result = self._check_sniper(ticker, entry_price, scores, stop_loss, target_price)
            elif strategy in ("VOYAGER", "VOY"):
                result = self._check_voyager(ticker, entry_price, scores, stop_loss, target_price)
            elif strategy in ("REMORA", "REM"):
                result = self._check_remora(ticker, entry_price, scores, stop_loss, target_price)
            else:
                result = self._check_generic(ticker, entry_price, scores, stop_loss, target_price)
        except Exception as exc:
            result = {
                "status":   NONE_ST,
                "strength": 0.5,
                "reason":   f"Thesis check error: {exc}",
                "checks":   {},
            }

        return result

    def check_all_positions(self, positions: list[dict]) -> list[dict]:
        """
        Bulk evaluate a list of position dicts.
        Each dict must have: ticker, strategy, entry_price.
        Optional: scores, stop_loss, target_price, trade_id.

        Returns list of result dicts with ticker/strategy added.
        """
        results = []
        for pos in positions:
            ticker   = pos.get("ticker", "")
            strategy = pos.get("strategy", "")
            entry    = float(pos.get("entry_price") or pos.get("avg_entry_price") or 0)
            scores   = pos.get("scores")
            sl       = pos.get("stop_loss")
            tp       = pos.get("target_price")
            trade_id = pos.get("trade_id")

            result = self.check_thesis(ticker, strategy, entry, scores, sl, tp)
            result["ticker"]   = ticker
            result["strategy"] = strategy
            result["trade_id"] = trade_id
            results.append(result)

        return results

    def log_thesis_event(
        self,
        ticker: str,
        to_status: str,
        from_status: Optional[str] = None,
        reason: str = "",
        strength: float = 0.5,
        trade_id: Optional[int] = None,
        run_id: Optional[str] = None,
        strategy: str = "",
        scores: Optional[dict] = None,
        db_path: str = DB_PATH,
    ) -> None:
        """Write a thesis transition event to thesis_events table."""
        scores = scores or {}
        ts = datetime.now(timezone.utc).isoformat()
        try:
            conn = sqlite3.connect(db_path)
            cur  = conn.cursor()
            # Check table exists (may not if migration hasn't run)
            cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='thesis_events'"
            )
            if not cur.fetchone():
                conn.close()
                return
            cur.execute("""
                INSERT INTO thesis_events
                    (timestamp, ticker, trade_id, run_id, from_status, to_status,
                     thesis_strength, reason, strategy,
                     composite_score, whale_score, tech_score, momentum_score)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                ts, ticker, trade_id, run_id,
                from_status, to_status, strength, reason, strategy,
                scores.get("composite_score"),
                scores.get("whale_score"),
                scores.get("technical_score"),
                scores.get("momentum_score"),
            ))
            conn.commit()
            conn.close()
        except Exception:
            pass  # never block caller

    # -----------------------------------------------------------------------
    # strategy-specific checkers
    # -----------------------------------------------------------------------

    def _check_sniper(self, ticker, entry_price, scores, stop_loss, target_price) -> dict:
        """
        SNIPER thesis: short-term momentum + volume surge.
        Breaks fast — uses MA crossover + volume trend.
        """
        checks = {}

        bars = self._get_bars(ticker, days_back=15)
        if not bars or len(bars) < 10:
            return self._fallback_result(entry_price, scores, "Insufficient bars for SNIPER check")

        closes  = [b["close"]  for b in bars]
        volumes = [b["volume"] for b in bars]
        current = closes[-1]

        # ── MA cross (5 vs 10) ──────────────────────────────────────────
        ma5  = sum(closes[-5:])  / 5
        ma10 = sum(closes[-10:]) / 10
        ma_bullish = ma5 > ma10
        checks["ma5_above_ma10"] = ma_bullish

        # ── volume trend (recent 3 vs prior 5) ──────────────────────────
        recent_vol   = sum(volumes[-3:]) / 3
        baseline_vol = sum(volumes[-8:-3]) / 5 if len(volumes) >= 8 else sum(volumes) / len(volumes)
        vol_ratio    = recent_vol / baseline_vol if baseline_vol > 0 else 1.0
        vol_healthy  = vol_ratio >= 0.8
        checks["volume_healthy"] = vol_healthy
        checks["volume_ratio"]   = round(vol_ratio, 2)

        # ── price vs entry ───────────────────────────────────────────────
        price_above_entry = current >= entry_price
        checks["price_above_entry"] = price_above_entry

        # ── momentum (last 3 closes) ─────────────────────────────────────
        up_days = sum(1 for i in range(-3, 0) if closes[i] > closes[i - 1])
        momentum_ok = up_days >= 2
        checks["recent_momentum_ok"] = momentum_ok
        checks["up_days_of_3"]       = up_days

        # ── stop proximity ───────────────────────────────────────────────
        if stop_loss and entry_price > stop_loss:
            stop_pct = (current - stop_loss) / (entry_price - stop_loss)
            checks["stop_buffer_pct"] = round(stop_pct, 2)
            near_stop = stop_pct < 0.20
        else:
            near_stop = False
            checks["stop_buffer_pct"] = None

        # ── composite ───────────────────────────────────────────────────
        pass_count = sum([ma_bullish, vol_healthy, price_above_entry, momentum_ok])

        if near_stop or (not ma_bullish and not momentum_ok):
            status   = BROKEN
            strength = 0.1 + 0.1 * pass_count
            reason   = "MA bearish + momentum lost" if not ma_bullish else "Near stop — thesis BROKEN"
        elif pass_count >= 3:
            status   = INTACT
            strength = 0.65 + 0.1 * (pass_count - 3)
            reason   = "SNIPER conditions intact"
        elif pass_count == 2:
            status   = WARN
            strength = 0.45
            reason   = "Mixed signals — SNIPER thesis weakening"
        else:
            status   = BROKEN
            strength = 0.15
            reason   = "Multiple SNIPER conditions failed"

        return {"status": status, "strength": min(1.0, strength), "reason": reason, "checks": checks}

    def _check_voyager(self, ticker, entry_price, scores, stop_loss, target_price) -> dict:
        """
        VOYAGER thesis: longer-duration trend following.
        Uses trend + accumulation/distribution flow regime.
        """
        checks = {}

        bars = self._get_bars(ticker, days_back=65)
        if not bars or len(bars) < 35:
            return self._fallback_result(entry_price, scores, "Insufficient bars for VOYAGER check")

        closes  = [b["close"]  for b in bars]
        volumes = [b["volume"] for b in bars]
        current = closes[-1]

        # ── trend (price vs MA20) ────────────────────────────────────────
        ma20 = sum(closes[-20:]) / 20
        above_ma20 = current > ma20
        checks["above_ma20"]  = above_ma20
        checks["ma20"]        = round(ma20, 2)

        # ── MA20 slope (last 5 days) ─────────────────────────────────────
        ma20_5d_ago = sum(closes[-25:-5]) / 20 if len(closes) >= 25 else ma20
        ma_trending_up = ma20 > ma20_5d_ago
        checks["ma20_trending_up"] = ma_trending_up

        # ── consecutive closes above entry ───────────────────────────────
        recent_closes = closes[-5:]
        above_entry   = sum(1 for c in recent_closes if c >= entry_price * 0.99)
        majority_above = above_entry >= 3
        checks["closes_above_entry_of_5"] = above_entry
        checks["majority_above_entry"]    = majority_above

        # ── accumulation / distribution flow regime ───────────────────────
        flow = self._voyager_flow_regime(bars)
        vol_confirmation = flow["up_down_volume_ratio"] >= 0.95
        accumulation_ok = flow["accumulation_score"] >= 55
        distribution_warn = flow["distribution_score"] >= 62
        distribution_broken = flow["distribution_score"] >= 75

        checks["volume_confirmation"] = vol_confirmation
        checks["accumulation_score"] = flow["accumulation_score"]
        checks["distribution_score"] = flow["distribution_score"]
        checks["accumulation_days"] = flow["accumulation_days"]
        checks["distribution_days"] = flow["distribution_days"]
        checks["up_down_volume_ratio"] = round(flow["up_down_volume_ratio"], 3)
        checks["obv_slope_norm"] = round(flow["obv_slope_norm"], 5)
        checks["adl_slope_norm"] = round(flow["adl_slope_norm"], 5)
        checks["flow_regime"] = flow["flow_regime"]

        # ── composite ───────────────────────────────────────────────────
        pass_count = sum([above_ma20, ma_trending_up, majority_above, vol_confirmation, accumulation_ok])

        if distribution_broken:
            status   = BROKEN
            strength = 0.10
            reason   = (
                "Distribution regime detected — VOYAGER thesis BROKEN "
                f"(dist={flow['distribution_score']:.0f})"
            )
        elif distribution_warn:
            status   = WARN
            strength = 0.32
            reason   = (
                "Distribution pressure rising — VOYAGER thesis at risk "
                f"(dist={flow['distribution_score']:.0f})"
            )
        elif not above_ma20 and not ma_trending_up:
            status   = BROKEN
            strength = 0.10
            reason   = "Below MA20 with declining trend — VOYAGER thesis BROKEN"
        elif pass_count >= 4:
            status   = INTACT
            strength = 0.70 + 0.06 * (pass_count - 4)
            reason   = (
                "VOYAGER thesis intact "
                f"(acc={flow['accumulation_score']:.0f}, dist={flow['distribution_score']:.0f})"
            )
        elif pass_count == 3:
            status   = WARN
            strength = 0.40
            reason   = "VOYAGER trend showing cracks"
        else:
            status   = BROKEN
            strength = 0.15
            reason   = "VOYAGER trend thesis invalidated"

        return {"status": status, "strength": min(1.0, strength), "reason": reason, "checks": checks}

    def _check_remora(self, ticker, entry_price, scores, stop_loss, target_price) -> dict:
        """
        REMORA thesis: momentum piggyback on institutional flow.
        Uses short-term momentum + score delta.
        """
        checks = {}

        bars = self._get_bars(ticker, days_back=10)
        if not bars or len(bars) < 5:
            return self._fallback_result(entry_price, scores, "Insufficient bars for REMORA check")

        closes = [b["close"] for b in bars]
        current = closes[-1]

        # ── recent momentum (last 3 bars) ────────────────────────────────
        up_days = sum(1 for i in range(-3, 0) if closes[i] > closes[i - 1])
        momentum_positive = up_days >= 2
        checks["momentum_positive"]  = momentum_positive
        checks["up_days_of_3"]       = up_days

        # ── price above entry ────────────────────────────────────────────
        price_ok = current >= entry_price * 0.99
        checks["price_above_entry"] = price_ok

        # ── score check (if available) ───────────────────────────────────
        composite = scores.get("composite_score", 50)
        score_ok  = composite >= 55
        checks["composite_score"]   = composite
        checks["score_sufficient"]  = score_ok

        # ── composite ───────────────────────────────────────────────────
        pass_count = sum([momentum_positive, price_ok, score_ok])

        if up_days == 0 and not price_ok:
            status   = BROKEN
            strength = 0.10
            reason   = "No momentum + below entry — REMORA thesis BROKEN"
        elif pass_count >= 2:
            status   = INTACT
            strength = 0.55 + 0.15 * (pass_count - 2)
            reason   = "REMORA flow momentum intact"
        else:
            status   = WARN
            strength = 0.30
            reason   = "REMORA momentum weakening"

        return {"status": status, "strength": min(1.0, strength), "reason": reason, "checks": checks}

    def _check_generic(self, ticker, entry_price, scores, stop_loss, target_price) -> dict:
        """Generic check for unknown/unrecognized strategies."""
        checks = {}

        bars = self._get_bars(ticker, days_back=10)
        current = bars[-1]["close"] if bars else entry_price
        price_ok = current >= entry_price * 0.98
        checks["price_above_entry"] = price_ok

        composite = scores.get("composite_score", 50)
        score_ok  = composite >= 50
        checks["score_sufficient"] = score_ok

        if price_ok and score_ok:
            return {"status": INTACT, "strength": 0.60, "reason": "Generic: price+score OK", "checks": checks}
        elif price_ok or score_ok:
            return {"status": WARN,   "strength": 0.40, "reason": "Generic: partial signals", "checks": checks}
        else:
            return {"status": BROKEN, "strength": 0.15, "reason": "Generic: price + score both degraded", "checks": checks}

    # -----------------------------------------------------------------------
    # helpers
    # -----------------------------------------------------------------------

    @staticmethod
    def _slope(values: list[float]) -> float:
        if len(values) < 2:
            return 0.0
        n = len(values)
        x = list(range(n))
        x_mean = sum(x) / n
        y_mean = sum(values) / n
        num = sum((x[i] - x_mean) * (values[i] - y_mean) for i in range(n))
        den = sum((x[i] - x_mean) ** 2 for i in range(n))
        return num / den if den else 0.0

    def _voyager_flow_regime(self, bars: list[dict]) -> dict:
        """Lightweight accumulation/distribution flow model for open-position checks."""
        def _clip(v: float, lo: float, hi: float) -> float:
            return max(lo, min(hi, v))

        window = bars[-50:] if len(bars) >= 50 else bars
        closes = [float(b["close"]) for b in window]
        highs = [float(b["high"]) for b in window]
        lows = [float(b["low"]) for b in window]
        vols = [float(b["volume"]) for b in window]

        avg_vol = (sum(vols[-20:]) / min(20, len(vols))) if vols else 0.0
        up_vol = 0.0
        down_vol = 0.0
        acc_days = 0
        dist_days = 0
        obv = 0.0
        adl = 0.0
        obv_series = [0.0]
        adl_series = [0.0]

        for i in range(1, len(window)):
            close = closes[i]
            prev_close = closes[i - 1]
            high = highs[i]
            low = lows[i]
            vol = vols[i]

            if close > prev_close:
                up_vol += vol
                obv += vol
            elif close < prev_close:
                down_vol += vol
                obv -= vol

            rng = max(high - low, 1e-6)
            clv = ((close - low) - (high - close)) / rng
            adl += clv * vol

            high_vol = vol >= avg_vol * 1.10 if avg_vol > 0 else False
            if close > prev_close and clv > 0.20 and high_vol:
                acc_days += 1
            if close < prev_close and clv < -0.20 and high_vol:
                dist_days += 1

            obv_series.append(obv)
            adl_series.append(adl)

        up_down_ratio = up_vol / max(down_vol, 1.0)
        down_up_ratio = down_vol / max(up_vol, 1.0)

        obv_slope_norm = self._slope(obv_series[-20:]) / max(avg_vol, 1.0)
        adl_slope_norm = self._slope(adl_series[-20:]) / max(avg_vol, 1.0)

        ma20 = sum(closes[-20:]) / min(20, len(closes))
        ma50 = sum(closes[-50:]) / min(50, len(closes))
        trend_damage = 0.0
        if closes[-1] < ma20:
            trend_damage += 4.0
        if closes[-1] < ma50:
            trend_damage += 6.0

        acc_score = 0.0
        acc_score += _clip((up_down_ratio - 0.9) * 24.0, 0.0, 30.0)
        acc_score += _clip(obv_slope_norm * 360.0, 0.0, 18.0)
        acc_score += _clip(adl_slope_norm * 340.0, 0.0, 18.0)
        acc_score += _clip((acc_days - dist_days + 2.0) * 2.5, 0.0, 20.0)
        acc_score += 14.0 if closes[-1] > ma50 else (8.0 if closes[-1] > ma20 else 0.0)

        dist_score = 0.0
        dist_score += _clip((down_up_ratio - 0.9) * 24.0, 0.0, 30.0)
        dist_score += _clip((-obv_slope_norm) * 360.0, 0.0, 18.0)
        dist_score += _clip((-adl_slope_norm) * 340.0, 0.0, 18.0)
        dist_score += _clip((dist_days - acc_days + 1.0) * 2.5, 0.0, 20.0)
        dist_score += trend_damage

        accumulation_score = round(_clip(acc_score, 0.0, 100.0), 1)
        distribution_score = round(_clip(dist_score, 0.0, 100.0), 1)

        if accumulation_score >= 65 and distribution_score <= 45:
            regime = "ACCUMULATION"
        elif distribution_score >= 65:
            regime = "DISTRIBUTION"
        else:
            regime = "MIXED"

        return {
            "accumulation_score": accumulation_score,
            "distribution_score": distribution_score,
            "up_down_volume_ratio": up_down_ratio,
            "accumulation_days": acc_days,
            "distribution_days": dist_days,
            "obv_slope_norm": obv_slope_norm,
            "adl_slope_norm": adl_slope_norm,
            "flow_regime": regime,
        }

    def _get_bars(self, ticker, days_back=20):
        try:
            return self._feed.get_daily_bars(ticker, days_back=days_back) if self._feed else []
        except Exception:
            return []

    @staticmethod
    def _fallback_result(entry_price, scores, reason) -> dict:
        return {
            "status":   NONE_ST,
            "strength": 0.5,
            "reason":   reason,
            "checks":   {},
        }


# ---------------------------------------------------------------------------
# standalone test
# ---------------------------------------------------------------------------

def _test():
    print("🧪 ThesisMonitor — standalone test (no live data)\n")

    monitor = ThesisMonitor(data_feed=None)

    # synthetic bars
    import random
    random.seed(42)
    def _bars(n=20, trend=0.002):
        bars = []
        p = 150.0
        for _ in range(n):
            p = p * (1 + trend + random.uniform(-0.01, 0.01))
            bars.append({
                "close":  round(p, 2),
                "high":   round(p * 1.005, 2),
                "low":    round(p * 0.995, 2),
                "volume": random.randint(800_000, 2_000_000),
            })
        return bars

    uptrend_bars = _bars(25, trend=0.003)

    # Monkey-patch feed
    class MockFeed:
        def get_daily_bars(self, ticker, days_back=20):
            return uptrend_bars[-days_back:]

    monitor._feed = MockFeed()

    entry = uptrend_bars[10]["close"]
    scores = {"composite_score": 72, "whale_score": 65, "technical_score": 75, "momentum_score": 80}

    for strat in ["SNIPER", "VOYAGER", "REMORA", "UNKNOWN"]:
        r = monitor.check_thesis("AAPL", strat, entry, scores, stop_loss=entry*0.95, target_price=entry*1.10)
        print(f"  {strat:10} → {r['status']:7} strength={r['strength']:.2f}  {r['reason']}")

    print("\n✅ ThesisMonitor test complete.")


if __name__ == "__main__":
    _test()
