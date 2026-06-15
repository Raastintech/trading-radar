"""
Short Risk Scorer — Short Interest + Squeeze Risk

Source: yfinance ticker.info (shortPercentOfFloat, shortRatio)
No paid API required.

Two faces of the score:
  • LONG / SNIPER positions:  high short interest = squeeze amplifier (GOOD)
  • SHORT positions:          high short interest = crowded risk (BAD)

squeeze_risk_score (0-100):
  50 = neutral
  >70 = meaningful short squeeze potential
  >85 = extreme squeeze setup (high conviction)

Usage:
    from short_risk_scorer import ShortRiskScorer
    sr = ShortRiskScorer()
    result = sr.score("GME")
    # result = {"squeeze_risk_score": 88.0, "short_float_pct": 22.5, ...}
"""

import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from typing import Optional

_RESULT_CACHE: dict = {}
_INFO_CACHE: dict = {}
_CACHE_TTL = 3600  # analyst/fundamental data changes slowly

DB_PATH = os.environ.get("TRADING_DB_PATH", "trading_performance.db")

def _cache_get(store: dict, key: str) -> Optional[dict]:
    entry = store.get(key)
    if entry and (time.time() - entry["_ts"]) < _CACHE_TTL:
        return entry["data"]
    return None


def _cache_set(store: dict, key: str, data) -> None:
    store[key] = {"data": data, "_ts": time.time()}


class ShortRiskScorer:
    """
    Score short interest and squeeze risk from yfinance data.
    """

    def score(self, ticker: str, save_to_db: bool = False) -> dict:
        """
        Returns:
            {
                "squeeze_risk_score":  float,   # 0-100; 50 = neutral
                "short_float_pct":     float | None,   # % of float shorted
                "days_to_cover":       float | None,   # short ratio
                "float_shares":        int | None,
                "signal":              str,     # EXTREME / HIGH / MODERATE / LOW / NEUTRAL
                "squeeze_potential":   bool,
                "crowded_risk":        bool,
                "note":                str,
            }
        """
        try:
            return self._score_impl(ticker, save_to_db=save_to_db)
        except Exception as exc:
            return self._neutral(ticker, note=f"Error: {exc}")

    # -----------------------------------------------------------------------
    # implementation
    # -----------------------------------------------------------------------

    def _score_impl(self, ticker: str, save_to_db: bool = False) -> dict:
        import yfinance as yf

        t = ticker.upper()
        cached = _cache_get(_RESULT_CACHE, t)
        if cached is not None:
            return cached

        info = _cache_get(_INFO_CACHE, t)
        if info is None:
            yfobj = yf.Ticker(t)
            info = yfobj.info or {}
            _cache_set(_INFO_CACHE, t, info)

        short_pct   = info.get("shortPercentOfFloat")   # e.g. 0.025 for 2.5%
        short_ratio = info.get("shortRatio")             # days to cover
        float_sh    = info.get("floatShares")

        # Normalise short_pct — yfinance returns as decimal (0.025 = 2.5%)
        if short_pct is not None:
            if short_pct > 1.0:           # sometimes returned as raw %
                short_pct = short_pct / 100.0
            short_float_pct = round(short_pct * 100.0, 2)
        else:
            short_float_pct = None

        if not short_float_pct and not short_ratio:
            result = self._neutral(t, note="No short interest data available")
            _cache_set(_RESULT_CACHE, t, result)
            if save_to_db:
                self._save_snapshot(t, result)
            return result

        # ── Score components ──────────────────────────────────────────────

        # Component 1: short % of float (0-50 pts)
        # 5% → ~15pts, 15% → ~35pts, 30%+ → 50pts
        if short_float_pct is not None:
            pct_pts = min(50.0, short_float_pct * 1.67)
        else:
            pct_pts = 0.0

        # Component 2: days-to-cover (0-30 pts)
        # 3 days → ~10pts, 10 days → ~30pts
        if short_ratio is not None:
            dtc_pts = min(30.0, float(short_ratio) * 3.0)
        else:
            dtc_pts = 0.0

        raw = pct_pts + dtc_pts  # 0-80

        # Normalize to 0-100 with 50 baseline
        # raw=0 → 0; raw=40 → 50 neutral; raw=80 → 100
        score = round((raw / 80.0) * 100.0, 1)

        # ── Signal tier ───────────────────────────────────────────────────
        if score >= 85:
            signal   = "EXTREME"
            squeeze  = True
            crowded  = True
        elif score >= 70:
            signal   = "HIGH"
            squeeze  = True
            crowded  = True
        elif score >= 55:
            signal   = "MODERATE"
            squeeze  = False
            crowded  = False
        elif score >= 30:
            signal   = "LOW"
            squeeze  = False
            crowded  = False
        else:
            signal   = "NEUTRAL"
            squeeze  = False
            crowded  = False

        note = (
            f"Short float: {'N/A' if short_float_pct is None else f'{short_float_pct:.1f}%'}, "
            f"DTC: {'N/A' if short_ratio is None else f'{short_ratio:.1f}d'}"
        )

        result = {
            "squeeze_risk_score": score,
            "short_float_pct":    short_float_pct,
            "days_to_cover":      round(float(short_ratio), 2) if short_ratio else None,
            "float_shares":       float_sh,
            "signal":             signal,
            "squeeze_potential":  squeeze,
            "crowded_risk":       crowded,
            "note":               note,
        }
        _cache_set(_RESULT_CACHE, t, result)
        if save_to_db:
            self._save_snapshot(t, result)
        return result

    @staticmethod
    def _neutral(ticker: str, note: str = "") -> dict:
        return {
            "squeeze_risk_score": 50.0,
            "short_float_pct":    None,
            "days_to_cover":      None,
            "float_shares":       None,
            "signal":             "NEUTRAL",
            "squeeze_potential":  False,
            "crowded_risk":       False,
            "note":               note or "No data",
        }

    @staticmethod
    def _save_snapshot(ticker: str, result: dict, db_path: str = DB_PATH) -> None:
        try:
            conn = sqlite3.connect(db_path)
            cur = conn.cursor()

            cur.execute("""
                CREATE TABLE IF NOT EXISTS short_interest (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp_utc TEXT NOT NULL,
                    ticker TEXT NOT NULL,
                    short_float REAL,
                    days_to_cover REAL,
                    borrow_rate REAL,
                    source TEXT,
                    raw_json TEXT
                )
            """)

            ts = datetime.now(timezone.utc).isoformat()
            cur.execute(
                """
                INSERT INTO short_interest
                    (timestamp_utc, ticker, short_float, days_to_cover, borrow_rate, source, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ts,
                    ticker,
                    result.get("short_float_pct"),
                    result.get("days_to_cover"),
                    None,
                    "yfinance",
                    json.dumps(result, default=str),
                ),
            )
            conn.commit()
            conn.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# standalone test
# ---------------------------------------------------------------------------

def _test():
    print("🧪 ShortRiskScorer — live test with yfinance\n")
    sr = ShortRiskScorer()
    for t in ["AAPL", "NVDA", "SPY"]:
        r = sr.score(t)
        print(
            f"  {t:6} score={r['squeeze_risk_score']:5.1f}  "
            f"{r['signal']:8}  {r['note']}"
        )
    print("\n✅ ShortRiskScorer test complete.")


if __name__ == "__main__":
    _test()
