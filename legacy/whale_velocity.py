"""
Whale Flow Velocity - Time-Weighted Urgency Detection

The Edge:
- $10M over 5 hours = Institutional accumulation (interesting)
- $10M in 5 minutes = URGENT action (sniper signal!)

Velocity Score = (Flow $ / Time Window) × Urgency Multiplier

Urgency Tiers:
- INSTANT (<5 min): 5x multiplier
- URGENT (<30 min): 3x multiplier
- FAST (<2 hrs): 2x multiplier
- ACCUMULATION (>2 hrs): 1x multiplier
"""

from datetime import datetime, timedelta
from typing import Dict, List


class WhaleVelocityTracker:
    """
    Track whale flow velocity for asymmetric edge

    Key Insight:
    Speed of capital deployment signals conviction level
    """

    def __init__(self):
        self.flow_windows = {
            "INSTANT": 5,
            "URGENT": 30,
            "FAST": 120,
            "ACCUMULATION": 999999,
        }

        self.urgency_multipliers = {
            "INSTANT": 5.0,
            "URGENT": 3.0,
            "FAST": 2.0,
            "ACCUMULATION": 1.0,
        }

        print("✅ Whale Velocity Tracker initialized")
        print("⚡ INSTANT flow (<5min) = 5x multiplier")

    def calculate_flow_velocity(self, trades: List[Dict]) -> Dict:
        """
        Calculate velocity-weighted whale score

        Args:
            trades: List of trades with timestamps and volumes
            [{
                'timestamp': datetime,
                'volume': int,
                'price': float,
                'side': 'buy' or 'sell'
            }]
        """

        if not trades or len(trades) < 2:
            return {
                "velocity_score": 0,
                "urgency_tier": "NONE",
                "reason": "Insufficient trades",
            }

        trades = sorted(trades, key=lambda x: x["timestamp"])
        first_trade = trades[0]["timestamp"]
        last_trade = trades[-1]["timestamp"]
        time_window = (last_trade - first_trade).total_seconds() / 60

        if time_window < 0.1:
            time_window = 0.1

        total_flow = sum(t["volume"] * t["price"] for t in trades)
        flow_per_minute = total_flow / time_window

        urgency_tier = "ACCUMULATION"
        if time_window <= self.flow_windows["INSTANT"]:
            urgency_tier = "INSTANT"
        elif time_window <= self.flow_windows["URGENT"]:
            urgency_tier = "URGENT"
        elif time_window <= self.flow_windows["FAST"]:
            urgency_tier = "FAST"

        multiplier = self.urgency_multipliers[urgency_tier]
        velocity_score = flow_per_minute * multiplier

        return {
            "velocity_score": round(velocity_score, 2),
            "urgency_tier": urgency_tier,
            "time_window_min": round(time_window, 1),
            "total_flow": round(total_flow, 2),
            "flow_per_minute": round(flow_per_minute, 2),
            "multiplier": multiplier,
            "trade_count": len(trades),
        }

    def enhance_whale_score(self, base_whale_score: float, velocity_data: Dict) -> Dict:
        """
        Enhance base whale score with velocity weighting
        """

        urgency_boosts = {
            "INSTANT": 0.50,
            "URGENT": 0.30,
            "FAST": 0.15,
            "ACCUMULATION": 0.0,
        }

        tier = velocity_data.get("urgency_tier", "ACCUMULATION")
        boost = urgency_boosts.get(tier, 0)
        enhanced_score = base_whale_score * (1 + boost)

        return {
            "base_whale_score": base_whale_score,
            "velocity_boost_pct": boost * 100,
            "enhanced_whale_score": round(enhanced_score, 1),
            "urgency_tier": tier,
            "reason": f"{tier} flow detected - {boost*100:.0f}% boost applied",
        }

    def score_ticker_from_live_feed(
        self,
        ticker: str,
        base_whale_score: float,
        window_seconds: float = 300.0,
    ) -> Dict:
        """
        Convenience method for the active V3 scoring path.

        Pulls SIP block trades from LiveFeed, converts them to the format
        expected by calculate_flow_velocity(), and returns an enhanced
        whale score dict ready to drop into any strategy scorer.

        Usage in any V3 scanner/scorer:
            from whale_velocity import WhaleVelocityTracker
            _vel = WhaleVelocityTracker()
            result = _vel.score_ticker_from_live_feed(ticker, base_score)
            final_score = result["enhanced_whale_score"]

        Returns the same dict as enhance_whale_score() plus a
        "live_feed_available" boolean and "block_print_count" int.
        No-op baseline (zero boost) is returned when LiveFeed has no
        block data or is not running.

        Note on `side` inference:
            calculate_flow_velocity() only uses timestamp, volume, and price —
            it does not use side.  Side is included for completeness but left
            as "unknown" since the SIP tape does not carry direction.
        """
        baseline = {
            "base_whale_score":    base_whale_score,
            "velocity_boost_pct":  0.0,
            "enhanced_whale_score": round(base_whale_score, 1),
            "urgency_tier":        "NONE",
            "reason":              "No block print data available",
            "live_feed_available": False,
            "block_print_count":   0,
        }

        try:
            from live_feed import LiveFeed
            prints = LiveFeed.get_block_trades(
                ticker, max_age_seconds=window_seconds, limit=50
            )
            if not prints:
                baseline["live_feed_available"] = True  # feed is up, just no blocks
                baseline["reason"] = "No block prints in window"
                return baseline

            # Convert to calculate_flow_velocity() format
            trades = []
            for p in prints:
                try:
                    from datetime import datetime as _dt
                    ts = _dt.fromisoformat(p["timestamp"]) if p.get("timestamp") else _dt.now()
                    trades.append({
                        "timestamp": ts,
                        "volume":    p["size"],
                        "price":     p["price"],
                        "side":      "unknown",   # SIP tape has no direction
                    })
                except Exception:
                    continue

            if not trades:
                return baseline

            velocity = self.calculate_flow_velocity(trades)
            enhanced = self.enhance_whale_score(base_whale_score, velocity)
            enhanced["live_feed_available"] = True
            enhanced["block_print_count"]   = len(trades)
            enhanced["velocity_detail"]     = velocity
            return enhanced

        except Exception:
            return baseline


def test_whale_velocity():
    """Test whale velocity scenarios"""

    print("🧪 Testing Whale Velocity Tracker...\n")

    tracker = WhaleVelocityTracker()

    print("=" * 80)
    print("Scenario 1: INSTANT Whale - $10M in 3 minutes")
    print("=" * 80)

    now = datetime.now()
    instant_trades = [
        {"timestamp": now, "volume": 50000, "price": 100, "side": "buy"},
        {"timestamp": now + timedelta(minutes=1), "volume": 30000, "price": 101, "side": "buy"},
        {"timestamp": now + timedelta(minutes=3), "volume": 20000, "price": 102, "side": "buy"},
    ]

    velocity = tracker.calculate_flow_velocity(instant_trades)
    enhanced = tracker.enhance_whale_score(75.0, velocity)

    print(f"\n⚡ Urgency: {velocity['urgency_tier']}")
    print(f"Time Window: {velocity['time_window_min']:.1f} minutes")
    print(f"Total Flow: ${velocity['total_flow']/1e6:.2f}M")
    print(f"Flow/Minute: ${velocity['flow_per_minute']/1e6:.2f}M")
    print(f"Multiplier: {velocity['multiplier']}x")
    print(f"\n📊 Score Enhancement:")
    print(f"Base Whale Score: {enhanced['base_whale_score']}")
    print(f"Velocity Boost: +{enhanced['velocity_boost_pct']:.0f}%")
    print(f"Enhanced Score: {enhanced['enhanced_whale_score']} ⭐")
    print()

    print("=" * 80)
    print("Scenario 2: ACCUMULATION - $10M over 5 hours")
    print("=" * 80)

    accumulation_trades = [
        {"timestamp": now, "volume": 20000, "price": 100, "side": "buy"},
        {"timestamp": now + timedelta(hours=2), "volume": 30000, "price": 101, "side": "buy"},
        {"timestamp": now + timedelta(hours=5), "volume": 50000, "price": 102, "side": "buy"},
    ]

    velocity2 = tracker.calculate_flow_velocity(accumulation_trades)
    enhanced2 = tracker.enhance_whale_score(75.0, velocity2)

    print(f"\n📈 Urgency: {velocity2['urgency_tier']}")
    print(f"Time Window: {velocity2['time_window_min']:.1f} minutes ({velocity2['time_window_min']/60:.1f} hours)")
    print(f"Total Flow: ${velocity2['total_flow']/1e6:.2f}M")
    print(f"Flow/Minute: ${velocity2['flow_per_minute']/1e6:.2f}M")
    print(f"Multiplier: {velocity2['multiplier']}x")
    print(f"\n📊 Score Enhancement:")
    print(f"Base Whale Score: {enhanced2['base_whale_score']}")
    print(f"Velocity Boost: +{enhanced2['velocity_boost_pct']:.0f}%")
    print(f"Enhanced Score: {enhanced2['enhanced_whale_score']}")
    print()

    print("=" * 80)
    print("💡 THE EDGE:")
    print("=" * 80)
    print("Same $10M flow, different speeds:")
    print(f"  INSTANT (3 min):  Enhanced Score = {enhanced['enhanced_whale_score']} (+50%)")
    print(f"  ACCUMULATION (5hr): Enhanced Score = {enhanced2['enhanced_whale_score']} (+0%)")
    print(f"\n⚡ Velocity gives you a {enhanced['enhanced_whale_score'] - enhanced2['enhanced_whale_score']:.0f} point edge!")
    print("=" * 80)


if __name__ == "__main__":
    test_whale_velocity()
