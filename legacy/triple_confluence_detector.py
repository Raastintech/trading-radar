"""
Triple Confluence Detector
Identifies confluence where multiple strategies independently agree.
"""

from typing import Any, Dict, List, Optional, Set, Tuple
from dataclasses import dataclass, field
import logging

logger = logging.getLogger(__name__)


@dataclass
class StrategySignal:
    """Normalized strategy signal used for confluence scoring."""
    ticker: str
    strategy: str
    direction: str
    score: Optional[float] = None
    risk_reward: Optional[float] = None
    source_type: str = "unknown"


@dataclass
class ConfluenceOpportunity:
    """Represents a confluence opportunity (double or triple)."""
    ticker: str
    confluence_level: int  # 2 or 3 strategies
    strategies: List[str]  # Which strategies found it
    conviction_score: float  # 0-100
    recommended_position_multiplier: float  # risk-aware guidance
    analysis: str
    direction: str = "LONG"
    strategy_scores: Dict[str, float] = field(default_factory=dict)
    avg_strategy_score: float = 0.0
    min_strategy_score: float = 0.0
    evidence_quality: str = "LOW"


class TripleConfluenceDetector:
    """
    Detect confluence across Voyager, Sniper, and Remora.

    Two operating modes:
    1) Legacy ticker-overlap mode (universe-only inputs)
    2) Signal-aware mode (actual scanner opportunities with score/direction)
    """

    STRATEGIES = ("VOYAGER", "SNIPER", "REMORA")
    STRATEGY_WEIGHTS = {
        "VOYAGER": 0.45,
        "SNIPER": 0.35,
        "REMORA": 0.20,
    }
    DEFAULT_SCORES = {
        "VOYAGER": 65.0,
        "SNIPER": 70.0,
        "REMORA": 68.0,
    }

    def __init__(self):
        print("💎 Triple Confluence Detector initialized")
        print("   Detecting score- and direction-aware confluence...")

    def analyze_confluence(
        self,
        voyager_candidates: List,
        sniper_candidates: List,
        remora_candidates: List
    ) -> Dict:
        """
        Legacy-compatible entrypoint.
        Accepts ticker lists or mixed payloads and computes confluence.
        """

        print("\n" + "="*80)
        print("💎 TRIPLE CONFLUENCE ANALYSIS")
        print("="*80 + "\n")
        voyager_signals = self._extract_signals(voyager_candidates, "VOYAGER", source_type="legacy")
        sniper_signals = self._extract_signals(sniper_candidates, "SNIPER", source_type="legacy")
        remora_signals = self._extract_signals(remora_candidates, "REMORA", source_type="legacy")
        return self._analyze_signals(
            voyager_signals,
            sniper_signals,
            remora_signals,
            coverage_label="Universe Coverage",
        )

    def analyze_from_opportunities(
        self,
        voyager_opportunities: List,
        sniper_opportunities: List,
        remora_opportunities: List,
    ) -> Dict:
        """
        Signal-aware confluence using actual approved scanner opportunities.
        """
        print("\n" + "="*80)
        print("💎 TRIPLE CONFLUENCE ANALYSIS (SIGNAL-AWARE)")
        print("="*80 + "\n")

        voyager_signals = self._extract_signals(voyager_opportunities, "VOYAGER", source_type="signal")
        sniper_signals = self._extract_signals(sniper_opportunities, "SNIPER", source_type="signal")
        remora_signals = self._extract_signals(remora_opportunities, "REMORA", source_type="signal")
        return self._analyze_signals(
            voyager_signals,
            sniper_signals,
            remora_signals,
            coverage_label="Approved Opportunity Coverage",
        )

    def _safe_float(self, value: Any) -> Optional[float]:
        try:
            if value is None:
                return None
            return float(value)
        except Exception:
            return None

    def _extract_signals(self, candidates: List, strategy: str, source_type: str) -> List[StrategySignal]:
        """Normalize varied candidate payloads into StrategySignal objects."""
        norm: List[StrategySignal] = []
        strategy_u = strategy.upper().strip()

        for item in candidates or []:
            ticker = ""
            direction_raw = None
            score = None
            rr = None

            if isinstance(item, dict):
                ticker = str(item.get("ticker") or item.get("symbol") or "").upper().strip()
                direction_raw = item.get("direction")
                score = self._safe_float(item.get("score", item.get("normalized_score")))
                rr = self._safe_float(item.get("risk_reward", item.get("rr")))
            elif isinstance(item, str):
                ticker = item.upper().strip()
            else:
                ticker = str(getattr(item, "ticker", "")).upper().strip()
                direction_raw = getattr(item, "direction", None)
                score = self._safe_float(getattr(item, "score", None))
                rr = self._safe_float(getattr(item, "risk_reward", None))

            if not ticker:
                continue

            direction = self._normalize_direction(direction_raw, strategy_u, source_type)
            if direction == "BOTH":
                norm.append(StrategySignal(ticker=ticker, strategy=strategy_u, direction="LONG", score=score, risk_reward=rr, source_type=source_type))
                norm.append(StrategySignal(ticker=ticker, strategy=strategy_u, direction="SHORT", score=score, risk_reward=rr, source_type=source_type))
            elif direction in ("LONG", "SHORT"):
                norm.append(StrategySignal(ticker=ticker, strategy=strategy_u, direction=direction, score=score, risk_reward=rr, source_type=source_type))

        return norm

    def _normalize_direction(self, raw_direction: Any, strategy: str, source_type: str) -> str:
        text = str(raw_direction or "").upper().strip()
        if text in {"LONG", "BUY", "BULL"}:
            return "LONG"
        if text in {"SHORT", "SELL", "BEAR"}:
            return "SHORT"
        if text in {"BOTH", "ANY"}:
            return "BOTH"

        # Legacy ticker-overlap mode often has no direction metadata.
        if source_type == "legacy":
            if strategy == "VOYAGER":
                return "BOTH"
            return "LONG"

        # Signal mode defaults: Sniper/Remora are long-only in this stack.
        if strategy in {"SNIPER", "REMORA"}:
            return "LONG"
        return "LONG"

    def _best_by_direction(self, signals: List[StrategySignal]) -> Dict[str, Dict[str, StrategySignal]]:
        best = {"LONG": {}, "SHORT": {}}
        for sig in signals:
            if sig.direction not in best:
                continue
            existing = best[sig.direction].get(sig.ticker)
            if existing is None:
                best[sig.direction][sig.ticker] = sig
                continue
            cur_score = self._effective_score(sig)
            old_score = self._effective_score(existing)
            if cur_score > old_score:
                best[sig.direction][sig.ticker] = sig
        return best

    def _effective_score(self, sig: StrategySignal) -> float:
        raw = self._safe_float(sig.score)
        if raw is not None:
            return max(0.0, min(raw, 100.0))
        return self.DEFAULT_SCORES.get(sig.strategy, 65.0)

    def _build_opportunity(
        self,
        ticker: str,
        direction: str,
        level: int,
        signals: List[StrategySignal],
    ) -> ConfluenceOpportunity:
        strategy_scores: Dict[str, float] = {}
        rr_vals: List[float] = []
        missing_scores = 0
        weighted_total = 0.0
        weight_sum = 0.0

        for sig in signals:
            score = self._safe_float(sig.score)
            if score is None:
                missing_scores += 1
            eff = self._effective_score(sig)
            strategy_scores[sig.strategy.lower()] = round(eff, 1)
            w = self.STRATEGY_WEIGHTS.get(sig.strategy, 0.33)
            weighted_total += eff * w
            weight_sum += w
            rr = self._safe_float(sig.risk_reward)
            if rr is not None and rr > 0:
                rr_vals.append(rr)

        avg_score = (sum(strategy_scores.values()) / len(strategy_scores)) if strategy_scores else 0.0
        min_score = min(strategy_scores.values()) if strategy_scores else 0.0
        weighted_score = (weighted_total / weight_sum) if weight_sum > 0 else avg_score
        rr_avg = (sum(rr_vals) / len(rr_vals)) if rr_vals else None

        conviction = weighted_score
        conviction -= 8.0 * missing_scores
        if min_score < 60:
            conviction -= 5.0
        if level == 2:
            conviction -= 4.0
        if rr_avg is not None:
            if rr_avg >= 3.0:
                conviction += 2.0
            elif rr_avg < 1.8:
                conviction -= 2.0

        conviction = max(0.0, min(conviction, 100.0))
        evidence_quality = "HIGH" if missing_scores == 0 else "MEDIUM" if missing_scores == 1 else "LOW"

        if level == 3:
            if conviction >= 85:
                multiplier = 1.6
            elif conviction >= 75:
                multiplier = 1.45
            elif conviction >= 65:
                multiplier = 1.30
            else:
                multiplier = 1.15
        else:
            if conviction >= 85:
                multiplier = 1.30
            elif conviction >= 75:
                multiplier = 1.20
            elif conviction >= 65:
                multiplier = 1.10
            else:
                multiplier = 1.00

        strategy_names = [s.strategy.lower() for s in signals]
        rr_str = f", avg R/R {rr_avg:.2f}" if rr_avg is not None else ""
        analysis = (
            f"{direction} confluence across {', '.join(strategy_names)} "
            f"(weighted {weighted_score:.1f}, min {min_score:.1f}{rr_str}, evidence {evidence_quality})."
        )

        return ConfluenceOpportunity(
            ticker=ticker,
            direction=direction,
            confluence_level=level,
            strategies=strategy_names,
            conviction_score=round(conviction, 1),
            recommended_position_multiplier=round(multiplier, 2),
            analysis=analysis,
            strategy_scores=strategy_scores,
            avg_strategy_score=round(avg_score, 1),
            min_strategy_score=round(min_score, 1),
            evidence_quality=evidence_quality,
        )

    def _analyze_signals(
        self,
        voyager_signals: List[StrategySignal],
        sniper_signals: List[StrategySignal],
        remora_signals: List[StrategySignal],
        coverage_label: str = "Source Coverage",
    ) -> Dict:
        voyager_idx = self._best_by_direction(voyager_signals)
        sniper_idx = self._best_by_direction(sniper_signals)
        remora_idx = self._best_by_direction(remora_signals)

        voyager_unique = {s.ticker for s in voyager_signals}
        sniper_unique = {s.ticker for s in sniper_signals}
        remora_unique = {s.ticker for s in remora_signals}
        total_unique = len(voyager_unique | sniper_unique | remora_unique)

        print(f"📊 {coverage_label}:")
        print(f"   Voyager: {len(voyager_unique)} symbols")
        print(f"   Sniper:  {len(sniper_unique)} symbols")
        print(f"   Remora:  {len(remora_unique)} symbols")
        print(f"   Total unique: {total_unique} symbols")

        triple_opportunities: List[ConfluenceOpportunity] = []
        double_opportunities: Dict[str, List[ConfluenceOpportunity]] = {
            "voyager_sniper": [],
            "voyager_remora": [],
            "sniper_remora": [],
        }
        triple_by_direction = {"LONG": 0, "SHORT": 0}

        for direction in ("LONG", "SHORT"):
            v = voyager_idx[direction]
            s = sniper_idx[direction]
            r = remora_idx[direction]

            triple_tickers = set(v) & set(s) & set(r)
            triple_by_direction[direction] = len(triple_tickers)
            for ticker in sorted(triple_tickers):
                opp = self._build_opportunity(
                    ticker=ticker,
                    direction=direction,
                    level=3,
                    signals=[v[ticker], s[ticker], r[ticker]],
                )
                triple_opportunities.append(opp)

            double_sets = {
                "voyager_sniper": (set(v) & set(s)) - triple_tickers,
                "voyager_remora": (set(v) & set(r)) - triple_tickers,
                "sniper_remora": (set(s) & set(r)) - triple_tickers,
            }

            for combo, tickers in double_sets.items():
                for ticker in sorted(tickers):
                    if combo == "voyager_sniper":
                        sigs = [v[ticker], s[ticker]]
                    elif combo == "voyager_remora":
                        sigs = [v[ticker], r[ticker]]
                    else:
                        sigs = [s[ticker], r[ticker]]

                    opp = self._build_opportunity(
                        ticker=ticker,
                        direction=direction,
                        level=2,
                        signals=sigs,
                    )
                    double_opportunities[combo].append(opp)

        triple_opportunities.sort(key=lambda x: (-x.conviction_score, x.ticker))
        for combo in list(double_opportunities.keys()):
            double_opportunities[combo].sort(key=lambda x: (-x.conviction_score, x.ticker))

        print("\n" + "="*80)
        print("📊 CONFLUENCE SUMMARY")
        print("="*80)
        print(f"💎 Triple: {len(triple_opportunities)} (LONG {triple_by_direction['LONG']}, SHORT {triple_by_direction['SHORT']})")
        print(
            f"🔗 Double: {sum(len(v) for v in double_opportunities.values())} "
            f"(VS {len(double_opportunities['voyager_sniper'])}, "
            f"VR {len(double_opportunities['voyager_remora'])}, "
            f"SR {len(double_opportunities['sniper_remora'])})"
        )

        return {
            "triple": triple_opportunities,
            "double": double_opportunities,
            "stats": {
                "triple_count": len(triple_opportunities),
                "double_count": sum(len(v) for v in double_opportunities.values()),
                "triple_long_count": triple_by_direction["LONG"],
                "triple_short_count": triple_by_direction["SHORT"],
                "total_unique": total_unique,
            },
        }


# Test function
if __name__ == "__main__":
    detector = TripleConfluenceDetector()

    # Mock data for testing
    voyager = [
        {"ticker": "AAPL", "direction": "LONG", "score": 82, "risk_reward": 3.2},
        {"ticker": "MSFT", "direction": "LONG", "score": 74, "risk_reward": 2.4},
        {"ticker": "GOOGL", "direction": "SHORT", "score": 78, "risk_reward": 2.1},
        {"ticker": "TWLO", "direction": "LONG", "score": 88, "risk_reward": 4.0},
    ]
    sniper = [
        {"ticker": "AAPL", "direction": "LONG", "score": 80, "risk_reward": 2.8},
        {"ticker": "MSFT", "direction": "LONG", "score": 71, "risk_reward": 2.5},
        {"ticker": "TWLO", "direction": "LONG", "score": 77, "risk_reward": 3.0},
    ]
    remora = [
        {"ticker": "AAPL", "direction": "LONG", "score": 76, "risk_reward": 2.2},
        {"ticker": "TWLO", "direction": "LONG", "score": 73, "risk_reward": 2.6},
        {"ticker": "GOOGL", "direction": "LONG", "score": 69, "risk_reward": 1.9},
    ]

    result = detector.analyze_confluence(voyager, sniper, remora)

    print("\n🎯 TRIPLE CONFLUENCE DETECTED:")
    for opp in result['triple']:
        print(
            f"   {opp.ticker} {opp.direction}: {opp.conviction_score:.1f}/100 "
            f"(x{opp.recommended_position_multiplier:.2f})"
        )
