"""
Volume Profile — POC / VAH / VAL / HVN / LVN

Source: Alpaca OHLCV daily bars (already fetched by AlpacaDataFeed).
No additional API calls needed.

Concepts:
  POC  — Point of Control: price level with highest volume
  VAH  — Value Area High: upper bound of 70% of total volume
  VAL  — Value Area Low: lower bound of 70% of total volume
  HVN  — High Volume Node: prices with significantly above-average volume
  LVN  — Low Volume Node: prices with significantly below-average volume

Volume profile quality:
  STRONG   — current price inside value area, near POC
  MODERATE — current price in value area, away from POC
  WEAK     — current price outside value area
  NONE     — insufficient data

Usage:
    from volume_profile import VolumeProfileAnalyzer
    vp = VolumeProfileAnalyzer(data_feed)
    result = vp.analyze("AAPL", days_back=60)
    # result = {"poc": 195.50, "vah": 202.30, "val": 187.80,
    #           "hvn": [...], "lvn": [...], "quality": "STRONG", ...}
"""

from typing import Optional

# Value area covers this fraction of total volume
VALUE_AREA_PCT = 0.70


class VolumeProfileAnalyzer:
    """
    Builds a daily-bar volume profile (price histogram weighted by volume).

    Each daily bar's volume is assigned to its VWAP proxy = (H+L+C)/3.
    Bins are created at $0.50 intervals (configurable via bin_size).
    """

    def __init__(self, data_feed=None, bin_size: float = 0.50):
        self._feed    = data_feed
        self._bin_size = bin_size

    # -----------------------------------------------------------------------
    # public API
    # -----------------------------------------------------------------------

    def analyze(
        self,
        ticker: str,
        days_back: int = 60,
        bars: Optional[list] = None,
    ) -> dict:
        """
        Build volume profile for ticker.

        Args:
            ticker:    equity symbol
            days_back: number of trading days to include
            bars:      pre-fetched bar list (skip fetch if provided)

        Returns dict with POC, VAH, VAL, HVN, LVN, quality, summary.
        """
        if bars is None:
            bars = self._fetch_bars(ticker, days_back)

        if not bars or len(bars) < 5:
            return self._empty(ticker, reason="Insufficient bars")

        current_price = bars[-1]["close"]

        # ── Build price → volume histogram ───────────────────────────────
        histogram = self._build_histogram(bars)
        if not histogram:
            return self._empty(ticker, reason="Empty histogram")

        # ── POC ───────────────────────────────────────────────────────────
        poc_price, poc_vol = max(histogram.items(), key=lambda x: x[1])

        # ── Value Area (70% of total volume around POC) ───────────────────
        vah, val = self._compute_value_area(histogram, poc_price)

        # ── HVN / LVN ─────────────────────────────────────────────────────
        avg_vol = sum(histogram.values()) / len(histogram)
        hvn = sorted(
            [p for p, v in histogram.items() if v >= avg_vol * 1.5],
            reverse=True,
        )[:5]
        lvn = sorted(
            [p for p, v in histogram.items() if v <= avg_vol * 0.5],
            reverse=True,
        )[:5]

        # ── Quality rating ────────────────────────────────────────────────
        quality, quality_reason = self._rate_quality(current_price, poc_price, vah, val)

        # ── Support / resistance from profile ─────────────────────────────
        # Nearest HVN below price = profile support
        support_hvn = max((p for p in hvn if p < current_price), default=None)
        # Nearest HVN above price = profile resistance
        resist_hvn  = min((p for p in hvn if p > current_price), default=None)

        return {
            "ticker":          ticker,
            "poc":             poc_price,
            "poc_volume":      poc_vol,
            "vah":             vah,
            "val":             val,
            "hvn":             hvn,
            "lvn":             lvn,
            "current_price":   current_price,
            "in_value_area":   val <= current_price <= vah,
            "profile_support": support_hvn,
            "profile_resist":  resist_hvn,
            "quality":         quality,
            "quality_reason":  quality_reason,
            "bars_used":       len(bars),
            "bin_count":       len(histogram),
            "bin_size":        self._bin_size,
        }

    # -----------------------------------------------------------------------
    # internal
    # -----------------------------------------------------------------------

    def _fetch_bars(self, ticker: str, days_back: int) -> list:
        try:
            return self._feed.get_daily_bars(ticker, days_back=days_back) if self._feed else []
        except Exception:
            return []

    def _build_histogram(self, bars: list) -> dict:
        """
        Map each bar's VWAP proxy → bin, accumulate volume.
        VWAP proxy = (high + low + close) / 3
        """
        hist = {}
        bs   = self._bin_size
        for bar in bars:
            try:
                vwap = (bar["high"] + bar["low"] + bar["close"]) / 3.0
                bin_price = round(round(vwap / bs) * bs, 2)
                hist[bin_price] = hist.get(bin_price, 0) + bar["volume"]
            except Exception:
                continue
        return hist

    def _compute_value_area(self, histogram: dict, poc_price: float) -> tuple[float, float]:
        """
        Expand outward from POC until 70% of total volume is captured.
        Returns (vah, val).
        """
        total_vol   = sum(histogram.values())
        target_vol  = total_vol * VALUE_AREA_PCT
        sorted_prices = sorted(histogram.keys())

        poc_idx = sorted_prices.index(poc_price) if poc_price in sorted_prices else len(sorted_prices) // 2

        captured = histogram.get(poc_price, 0)
        lo_idx   = poc_idx
        hi_idx   = poc_idx

        while captured < target_vol:
            expand_up   = hi_idx + 1 < len(sorted_prices)
            expand_down = lo_idx - 1 >= 0

            if not expand_up and not expand_down:
                break

            up_vol   = histogram.get(sorted_prices[hi_idx + 1], 0) if expand_up   else 0
            down_vol = histogram.get(sorted_prices[lo_idx - 1], 0) if expand_down else 0

            if up_vol >= down_vol and expand_up:
                hi_idx  += 1
                captured += up_vol
            elif expand_down:
                lo_idx  -= 1
                captured += down_vol
            else:
                hi_idx  += 1
                captured += up_vol

        vah = sorted_prices[hi_idx]
        val = sorted_prices[lo_idx]
        return vah, val

    def _rate_quality(
        self, current: float, poc: float, vah: float, val: float
    ) -> tuple[str, str]:
        in_va   = val <= current <= vah
        poc_pct = abs(current - poc) / poc if poc > 0 else 1.0

        if in_va and poc_pct < 0.02:
            return "STRONG", "Price at POC inside value area"
        elif in_va and poc_pct < 0.05:
            return "STRONG", "Price near POC inside value area"
        elif in_va:
            return "MODERATE", "Price inside value area, away from POC"
        elif poc_pct < 0.05:
            return "MODERATE", "Price near POC but outside value area"
        else:
            return "WEAK", f"Price outside value area ({current:.2f} vs VA {val:.2f}-{vah:.2f})"

    @staticmethod
    def _empty(ticker: str, reason: str = "") -> dict:
        return {
            "ticker":          ticker,
            "poc":             None,
            "poc_volume":      None,
            "vah":             None,
            "val":             None,
            "hvn":             [],
            "lvn":             [],
            "current_price":   None,
            "in_value_area":   False,
            "profile_support": None,
            "profile_resist":  None,
            "quality":         "NONE",
            "quality_reason":  reason,
            "bars_used":       0,
            "bin_count":       0,
            "bin_size":        0.50,
        }


# ---------------------------------------------------------------------------
# standalone test
# ---------------------------------------------------------------------------

def _test():
    print("🧪 VolumeProfileAnalyzer — synthetic test\n")

    import random
    random.seed(99)

    def _bars(n=60, start=150.0, trend=0.001):
        bars = []
        p = start
        for _ in range(n):
            p *= (1 + trend + random.uniform(-0.012, 0.012))
            h = p * (1 + random.uniform(0, 0.008))
            l = p * (1 - random.uniform(0, 0.008))
            vol = random.randint(500_000, 3_000_000)
            bars.append({"close": round(p, 2), "high": round(h, 2),
                         "low": round(l, 2), "volume": vol})
        return bars

    vpa = VolumeProfileAnalyzer(data_feed=None, bin_size=0.50)
    result = vpa.analyze("TEST", bars=_bars(60))

    print(f"  POC:           ${result['poc']}")
    print(f"  VAH / VAL:     ${result['vah']} / ${result['val']}")
    print(f"  Current price: ${result['current_price']}")
    print(f"  In value area: {result['in_value_area']}")
    print(f"  Quality:       {result['quality']} — {result['quality_reason']}")
    print(f"  HVN (top 3):   {result['hvn'][:3]}")
    print(f"  Profile support: {result['profile_support']}")
    print(f"  Profile resist:  {result['profile_resist']}")

    print("\n✅ VolumeProfileAnalyzer test complete.")


if __name__ == "__main__":
    _test()
