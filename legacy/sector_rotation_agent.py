from typing import Dict

from sector_rs import SectorRS


class SectorRotationAgent:
    def __init__(self):
        self.rs = SectorRS()

    def vote(self, ticker: str) -> Dict:
        rs = self.rs.compute(ticker)
        etf = rs.get("sector_etf", "SPY")
        verdict = rs.get("verdict", "NEUTRAL")
        conf = float(rs.get("confidence", 0.0) or 0.0)
        gap_pct = float(rs.get("gap_pct", 0.0) or 0.0)
        rs_slope = float(rs.get("rs_slope", 0.0) or 0.0)

        if not rs.get("ok", False):
            return {
                "vote": "ABSTAIN",
                "reason": f"RS unavailable ({etf}): {rs.get('reason')}",
                "score": None,
                "confidence": 0.0,
                "details": rs,
            }

        # Never VETO on incomplete RS payload.
        if rs.get("rs_ratio") is None or rs.get("rs_ma10") is None:
            return {
                "vote": "ABSTAIN",
                "reason": f"RS incomplete ({etf})",
                "score": None,
                "confidence": 0.0,
                "details": rs,
            }

        # HARD VETO: severe collapse only.
        if verdict == "HEADWIND":
            if (
                gap_pct < -8.0
                and rs_slope < -0.015
                and conf >= 0.70
            ):
                return {
                    "vote": "VETO",
                    "reason": f"Severe sector collapse ({etf}): gap {gap_pct:.1f}%, slope {rs_slope:.3f}",
                    "score": 15,
                    "confidence": conf,
                    "details": rs,
                }

            # CAUTION: moderate headwind.
            if gap_pct < -3.0 or (conf >= 0.4 and rs_slope < -0.005):
                return {
                    "vote": "CAUTION",
                    "reason": f"Moderate sector headwind ({etf}): gap {gap_pct:.1f}%, slope {rs_slope:.3f}",
                    "score": 40,
                    "confidence": conf,
                    "details": rs,
                }

        if verdict == "TAILWIND":
            return {
                "vote": "APPROVE",
                "reason": f"Sector tailwind ({etf}): gap {gap_pct:.1f}%, slope {rs_slope:.3f}",
                "score": 75,
                "confidence": conf,
                "details": rs,
            }

        # NEUTRAL or weak headwind.
        if verdict == "NEUTRAL" or (verdict == "HEADWIND" and gap_pct >= -3.0):
            return {
                "vote": "APPROVE",
                "reason": f"Sector {verdict.lower()} ({etf}): gap {gap_pct:.1f}%",
                "score": 55,
                "confidence": conf,
                "details": rs,
            }

        # Fallback.
        return {
            "vote": "CAUTION",
            "reason": f"Sector {verdict} ({etf}), unclear severity",
            "score": 50,
            "confidence": conf,
            "details": rs,
        }
