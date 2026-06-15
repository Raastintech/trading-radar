"""
Revision Scorer — EPS Revision Momentum via yfinance

Signals:
  - Analyst upgrades vs downgrades in last 90 days (recommendations df)
  - Target price premium: (mean_target / current_price - 1)
  - Target price consensus dispersion (std/mean)

Score: 0-100, higher = more bullish analyst activity

Usage:
    from revision_scorer import RevisionScorer
    rs = RevisionScorer()
    result = rs.score("AAPL")
    # result = {"revision_score": 72.0, "upgrades": 4, "downgrades": 1, ...}
"""

import time
from typing import Optional

_INFO_CACHE: dict = {}
_REC_CACHE:  dict = {}
_CACHE_TTL = 3600  # 1 hour — analyst data changes slowly


def _cache_get(store: dict, key: str, ttl: float) -> Optional[dict]:
    entry = store.get(key)
    if entry and (time.time() - entry["_ts"]) < ttl:
        return entry["data"]
    return None


def _cache_set(store: dict, key: str, data) -> None:
    store[key] = {"data": data, "_ts": time.time()}


class RevisionScorer:
    """
    Score analyst revision momentum from yfinance data.
    Falls back gracefully to score=50 when data is unavailable.
    """

    LOOKBACK_DAYS = 90

    def score(self, ticker: str) -> dict:
        """
        Returns:
            {
                "revision_score":   float,   # 0-100
                "upgrades":         int,
                "downgrades":       int,
                "net_upgrades":     int,
                "target_premium":   float,   # (mean_target/price - 1), may be None
                "target_dispersion":float,   # std/mean, may be None
                "mean_target":      float | None,
                "current_price":    float | None,
                "data_quality":     str,     # FULL / PARTIAL / NONE
                "note":             str,
            }
        """
        try:
            return self._score_impl(ticker)
        except Exception as exc:
            return self._neutral(ticker, note=f"Error: {exc}")

    # -----------------------------------------------------------------------
    # implementation
    # -----------------------------------------------------------------------

    def _score_impl(self, ticker: str) -> dict:
        import yfinance as yf
        from datetime import datetime, timedelta

        t = ticker.upper()

        # ── recommendations (upgrades/downgrades) ───────────────────────
        rec_result = _cache_get(_REC_CACHE, t, _CACHE_TTL)
        if rec_result is None:
            try:
                yfobj  = yf.Ticker(t)
                rec_df = yfobj.upgrades_downgrades
                if rec_df is not None and not rec_df.empty:
                    _cache_set(_REC_CACHE, t, rec_df)
                    rec_result = rec_df
            except Exception:
                rec_result = None

        upgrades   = 0
        downgrades = 0

        if rec_result is not None:
            try:
                cutoff = datetime.now() - timedelta(days=self.LOOKBACK_DAYS)
                # Index may be a DatetimeIndex or GradeDate column
                df = rec_result.copy()
                if df.index.name and "date" in df.index.name.lower():
                    df = df[df.index >= cutoff]
                elif "GradeDate" in df.columns:
                    df["GradeDate"] = df["GradeDate"].apply(
                        lambda x: x if hasattr(x, "year") else None
                    )
                    df = df[df["GradeDate"] >= cutoff]

                # Upgrade markers
                if "ToGrade" in df.columns:
                    up_words   = ["buy", "overweight", "outperform", "strong buy", "positive"]
                    down_words = ["sell", "underweight", "underperform", "reduce", "negative"]
                    for grade in df["ToGrade"].dropna().str.lower():
                        if any(w in grade for w in up_words):
                            upgrades += 1
                        elif any(w in grade for w in down_words):
                            downgrades += 1
                elif "Action" in df.columns:
                    for action in df["Action"].dropna().str.lower():
                        if "up" in action:
                            upgrades += 1
                        elif "down" in action:
                            downgrades += 1
            except Exception:
                pass

        # ── analyst price target premium ─────────────────────────────────
        info_data = _cache_get(_INFO_CACHE, t, _CACHE_TTL)
        if info_data is None:
            try:
                yfobj     = yf.Ticker(t)
                info_data = yfobj.info or {}
                _cache_set(_INFO_CACHE, t, info_data)
            except Exception:
                info_data = {}

        mean_target   = info_data.get("targetMeanPrice")
        low_target    = info_data.get("targetLowPrice")
        high_target   = info_data.get("targetHighPrice")
        current_price = info_data.get("currentPrice") or info_data.get("regularMarketPrice")

        target_premium    = None
        target_dispersion = None

        if mean_target and current_price and current_price > 0:
            target_premium = round((mean_target / current_price) - 1.0, 4)

        if mean_target and low_target and high_target and mean_target > 0:
            # simple range-based dispersion proxy
            spread = high_target - low_target
            target_dispersion = round(spread / mean_target, 4)

        # ── composite score ───────────────────────────────────────────────
        net  = upgrades - downgrades
        total = upgrades + downgrades

        if total == 0 and target_premium is None:
            return self._neutral(ticker, note="No analyst data")

        # Start at 50; analyst activity component
        score = 50.0

        if total > 0:
            # Net ratio: +1 = all upgrades, -1 = all downgrades
            net_ratio = net / total  # -1.0 to +1.0
            score += net_ratio * 25.0  # ±25 pts from analyst sentiment

        # Target premium component (max ±20 pts)
        if target_premium is not None:
            # e.g., +15% upside → +20 pts;  -15% → -20 pts
            premium_pts = min(20.0, max(-20.0, target_premium * 100.0 * 1.33))
            score += premium_pts

        # Volume of activity (up to +5 for high conviction)
        if total >= 10:
            score += 5.0
        elif total >= 5:
            score += 2.5

        # Dispersion penalty (high dispersion = uncertain = down to -5)
        if target_dispersion is not None and target_dispersion > 0.30:
            score -= min(5.0, (target_dispersion - 0.30) * 20.0)

        score = round(max(0.0, min(100.0, score)), 1)

        data_quality = "FULL" if (total > 0 and target_premium is not None) else "PARTIAL"
        note = (
            f"{upgrades}U/{downgrades}D last {self.LOOKBACK_DAYS}d; "
            f"target {'N/A' if target_premium is None else f'{target_premium*100:+.1f}%'} premium"
        )

        return {
            "revision_score":    score,
            "upgrades":          upgrades,
            "downgrades":        downgrades,
            "net_upgrades":      net,
            "target_premium":    target_premium,
            "target_dispersion": target_dispersion,
            "mean_target":       mean_target,
            "current_price":     current_price,
            "data_quality":      data_quality,
            "note":              note,
        }

    @staticmethod
    def _neutral(ticker: str, note: str = "") -> dict:
        return {
            "revision_score":    50.0,
            "upgrades":          0,
            "downgrades":        0,
            "net_upgrades":      0,
            "target_premium":    None,
            "target_dispersion": None,
            "mean_target":       None,
            "current_price":     None,
            "data_quality":      "NONE",
            "note":              note or "No data",
        }


# ---------------------------------------------------------------------------
# standalone test
# ---------------------------------------------------------------------------

def _test():
    print("🧪 RevisionScorer — live test with yfinance\n")
    rs = RevisionScorer()
    for t in ["AAPL", "NVDA", "SPY"]:
        r = rs.score(t)
        print(f"  {t:6} score={r['revision_score']:5.1f}  {r['note']}")
    print("\n✅ RevisionScorer test complete.")


if __name__ == "__main__":
    _test()
