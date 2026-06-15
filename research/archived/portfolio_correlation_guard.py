"""
portfolio_correlation_guard.py — Phase 5 additive portfolio overlap guard.

Uses sector concentration and high-beta load as lightweight correlation proxies.
No covariance matrix required.
"""

from typing import Dict, List, Tuple

SECTOR_CONCENTRATION_LIMIT = 0.40  # max 40% of open positions in same sector
HIGH_BETA_LIMIT = 3                # max simultaneous beta>1.5 positions
HIGH_BETA_THRESHOLD = 1.5


class PortfolioCorrelationGuard:
    """Simple, deterministic pre-trade overlap guard."""

    def __init__(
        self,
        sector_concentration_limit: float = SECTOR_CONCENTRATION_LIMIT,
        high_beta_limit: int = HIGH_BETA_LIMIT,
        high_beta_threshold: float = HIGH_BETA_THRESHOLD,
    ):
        self.sector_concentration_limit = float(sector_concentration_limit)
        self.high_beta_limit = int(high_beta_limit)
        self.high_beta_threshold = float(high_beta_threshold)

    @staticmethod
    def _norm_sector(value) -> str:
        txt = str(value or "UNKNOWN").strip().upper()
        return txt or "UNKNOWN"

    @staticmethod
    def _as_float(value, default: float = 0.0) -> float:
        try:
            if value is None:
                return float(default)
            return float(value)
        except Exception:
            return float(default)

    def check(
        self,
        new_ticker: str,
        new_sector: str,
        new_beta: float,
        open_positions: List[Dict],
    ) -> Tuple[bool, str]:
        """
        Returns (allowed: bool, reason: str).

        reason values:
          - "OK"
          - "SECTOR_CONCENTRATION"
          - "HIGH_BETA_OVERLOAD"
        """
        rows = [dict(r or {}) for r in (open_positions or [])]
        nt = str(new_ticker or "").upper()
        ns = self._norm_sector(new_sector)
        nb = self._as_float(new_beta, 1.0)

        # Ignore same ticker if already present in open positions.
        existing = [r for r in rows if str(r.get("ticker") or "").upper() != nt]

        # --- High beta overload check ---
        high_beta_count = 0
        for row in existing:
            beta = self._as_float(row.get("beta"), 0.0)
            if beta > self.high_beta_threshold:
                high_beta_count += 1
        if nb > self.high_beta_threshold:
            high_beta_count += 1
        if high_beta_count > self.high_beta_limit:
            return False, "HIGH_BETA_OVERLOAD"

        # --- Sector concentration check ---
        total_after = len(existing) + 1
        same_sector_after = sum(
            1
            for row in existing
            if self._norm_sector(row.get("sector")) == ns
        ) + 1

        # Concentration becomes meaningful only once portfolio has breadth.
        if total_after >= 3:
            sector_ratio = same_sector_after / float(total_after)
            if sector_ratio > self.sector_concentration_limit:
                return False, "SECTOR_CONCENTRATION"

        return True, "OK"
