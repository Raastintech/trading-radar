"""
Shared dynamic universe snapshot builder.

Builds one truthful market snapshot from Alpaca asset discovery plus Alpaca bar data,
then routes the same base universe into strategy-specific candidate pools.
"""

from __future__ import annotations

import json
import logging
import math
import os
import tempfile
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence

from config import TradingConfig

try:
    from alpaca.trading.requests import GetAssetsRequest
    from alpaca.trading.enums import AssetClass, AssetStatus
except Exception:  # pragma: no cover - import guard for test doubles
    GetAssetsRequest = None  # type: ignore
    AssetClass = None  # type: ignore
    AssetStatus = None  # type: ignore

logger = logging.getLogger(__name__)


def _clip01(value: float) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except Exception:
        return 0.0


def _log_norm(value: float, lo: float, hi: float) -> float:
    if value <= 0 or hi <= lo:
        return 0.0
    try:
        lv = math.log10(float(value))
    except Exception:
        return 0.0
    return _clip01((lv - lo) / (hi - lo))


def _safe_pct(current: float, prior: float) -> float:
    if prior == 0:
        return 0.0
    return ((current - prior) / prior) * 100.0


class UniverseSnapshotBuilder:
    """
    Build a single shared dynamic market universe and strategy-specific routes.

    The output is deterministic, auditable, and cached for a short interval so the
    system does not rebuild the whole market snapshot on every call.
    """

    DEFAULT_BASE_LIMIT = int(os.getenv("UNIVERSE_BASE_LIMIT", "1000"))
    DEFAULT_VOYAGER_LIMIT = int(os.getenv("UNIVERSE_VOYAGER_LIMIT", "90"))
    DEFAULT_SNIPER_LIMIT = int(os.getenv("UNIVERSE_SNIPER_LIMIT", "90"))
    DEFAULT_SHORT_LIMIT = int(os.getenv("UNIVERSE_SHORT_LIMIT", "90"))
    DEFAULT_REMORA_LIMIT = int(os.getenv("UNIVERSE_REMORA_LIMIT", "60"))
    DEFAULT_CONTRARIAN_LIMIT = int(os.getenv("UNIVERSE_CONTRARIAN_LIMIT", "90"))
    SNAPSHOT_TTL_SECONDS = int(os.getenv("UNIVERSE_SNAPSHOT_TTL_SECONDS", "1800"))
    DISCOVERY_DAYS_BACK = int(os.getenv("UNIVERSE_DISCOVERY_DAYS_BACK", "90"))
    BATCH_CHUNK_SIZE = int(os.getenv("UNIVERSE_BATCH_CHUNK_SIZE", "200"))
    MIN_PRICE = float(os.getenv("UNIVERSE_MIN_PRICE", "5"))
    MAX_PRICE = float(os.getenv("UNIVERSE_MAX_PRICE", "1000"))
    MIN_AVG_VOLUME = float(os.getenv("UNIVERSE_MIN_AVG_VOLUME", "300000"))
    MIN_AVG_DOLLAR_VOLUME = float(os.getenv("UNIVERSE_MIN_AVG_DOLLAR_VOLUME", "5000000"))

    def __init__(
        self,
        data_feed,
        trading_client=None,
        reports_dir: str = "reports",
        curated_symbols: Optional[Sequence[str]] = None,
    ):
        self.data_feed = data_feed
        self.trading_client = trading_client
        self.reports_dir = reports_dir
        self._last_snapshot: Optional[Dict] = None
        self._last_snapshot_ts: float = 0.0
        self._curated_symbols = self._resolve_curated_symbols(curated_symbols)

    def build_snapshot(self, force_refresh: bool = False) -> Dict:
        if (
            not force_refresh
            and self._last_snapshot
            and (time.time() - self._last_snapshot_ts) < self.SNAPSHOT_TTL_SECONDS
        ):
            return self._last_snapshot

        snapshot = self._build_snapshot_fresh()
        self._last_snapshot = snapshot
        self._last_snapshot_ts = time.time()
        self._persist_snapshot(snapshot)
        return snapshot

    def _build_snapshot_fresh(self) -> Dict:
        discovered_assets = self._discover_assets()
        if not discovered_assets:
            return self._empty_snapshot("asset_discovery_failed")
        source_assets, overlay_added_symbols = self._apply_curated_overlay(discovered_assets)

        bars_by_symbol = self.data_feed.get_daily_bars_batch(
            source_assets,
            days_back=self.DISCOVERY_DAYS_BACK,
            adjustment="all",
            chunk_size=self.BATCH_CHUNK_SIZE,
        )

        metrics = []
        excluded_for_data = 0
        excluded_for_filters = 0
        passed_symbols = set()

        for symbol in source_assets:
            bars = bars_by_symbol.get(symbol) or []
            feature_row = self._compute_features(symbol, bars)
            if feature_row is None:
                excluded_for_data += 1
                continue
            if not self._passes_basic_filters(feature_row):
                excluded_for_filters += 1
                continue
            passed_symbols.add(symbol)
            metrics.append(feature_row)

        if not metrics:
            return self._empty_snapshot("no_symbols_passed_market_filters", source_count=len(source_assets))

        metrics.sort(key=lambda row: (-row["base_score"], row["symbol"]))
        base_rows = metrics[: self.DEFAULT_BASE_LIMIT]
        base_symbols = {row["symbol"] for row in base_rows}
        curated_rows = [
            row for row in metrics
            if row["symbol"] in self._curated_symbols and row["symbol"] not in base_symbols
        ]
        if curated_rows:
            base_rows = list(base_rows) + curated_rows
            base_rows.sort(key=lambda row: (-row["base_score"], row["symbol"]))

        routed = {
            "voyager": self._route_symbols(base_rows, "voyager_score", self.DEFAULT_VOYAGER_LIMIT, self._voyager_filter),
            "sniper": self._route_symbols(base_rows, "sniper_score", self.DEFAULT_SNIPER_LIMIT, self._sniper_filter),
            "short": self._route_symbols(base_rows, "short_score", self.DEFAULT_SHORT_LIMIT, self._short_filter),
            "remora": self._route_symbols(base_rows, "remora_score", self.DEFAULT_REMORA_LIMIT, self._remora_filter),
            "contrarian": self._route_symbols(base_rows, "contrarian_score", self.DEFAULT_CONTRARIAN_LIMIT, self._contrarian_filter),
        }

        base_symbols = [row["symbol"] for row in base_rows]
        overlay_added_set = set(overlay_added_symbols)
        curated_included = [symbol for symbol in base_symbols if symbol in self._curated_symbols]
        curated_overlay_symbols = [symbol for symbol in base_symbols if symbol in overlay_added_set]
        curated_missing = [symbol for symbol in self._curated_symbols if symbol not in passed_symbols]
        metadata = {
            row["symbol"]: {
                "price": round(row["price"], 4),
                "avg_volume_20": int(row["avg_volume_20"]),
                "avg_dollar_volume_20": round(row["avg_dollar_volume_20"], 2),
                "atr_pct_14": round(row["atr_pct_14"], 3),
                "return_20d_pct": round(row["return_20d_pct"], 3),
                "return_5d_pct": round(row["return_5d_pct"], 3),
                "volume_ratio_5d": round(row["volume_ratio_5d"], 3),
                "close_vs_ma20_pct": round(row["close_vs_ma20_pct"], 3),
                "dist_to_20d_high_pct": round(row["dist_to_20d_high_pct"], 3),
                "dist_to_20d_low_pct": round(row["dist_to_20d_low_pct"], 3),
                "scores": {
                    "base": round(row["base_score"], 4),
                    "voyager": round(row["voyager_score"], 4),
                    "sniper": round(row["sniper_score"], 4),
                    "short": round(row["short_score"], 4),
                    "remora": round(row["remora_score"], 4),
                    "contrarian": round(row["contrarian_score"], 4),
                },
            }
            for row in base_rows
        }

        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": "alpaca_dynamic_snapshot",
            "fallback_used": False,
            "fallback_reason": None,
            "base_universe": base_symbols,
            "voyager_universe": routed["voyager"],
            "sniper_universe": routed["sniper"],
            "short_universe": routed["short"],
            "remora_universe": routed["remora"],
            "contrarian_universe": routed["contrarian"],
            "summary": {
                "source_assets": len(discovered_assets),
                "source_assets_with_curated_overlay": len(source_assets),
                "passed_basic_filters": len(metrics),
                "excluded_for_data": excluded_for_data,
                "excluded_for_filters": excluded_for_filters,
                "base_universe_size": len(base_symbols),
                "voyager_universe_size": len(routed["voyager"]),
                "sniper_universe_size": len(routed["sniper"]),
                "short_universe_size": len(routed["short"]),
                "remora_universe_size": len(routed["remora"]),
                "contrarian_universe_size": len(routed["contrarian"]),
                "curated_requested": len(self._curated_symbols),
                "curated_included": len(curated_included),
                "curated_overlay_added": len(curated_overlay_symbols),
                "curated_missing": len(curated_missing),
            },
            "top_symbols": base_symbols[:25],
            "curated_overlay_symbols": curated_overlay_symbols,
            "curated_missing_symbols": curated_missing,
            "metadata": metadata,
        }

    def _empty_snapshot(self, reason: str, source_count: int = 0) -> Dict:
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": "alpaca_dynamic_snapshot",
            "fallback_used": True,
            "fallback_reason": reason,
            "base_universe": [],
            "voyager_universe": [],
            "sniper_universe": [],
            "short_universe": [],
            "remora_universe": [],
            "contrarian_universe": [],
            "summary": {
                "source_assets": source_count,
                "passed_basic_filters": 0,
                "excluded_for_data": 0,
                "excluded_for_filters": 0,
                "base_universe_size": 0,
                "voyager_universe_size": 0,
                "sniper_universe_size": 0,
                "short_universe_size": 0,
                "remora_universe_size": 0,
                "contrarian_universe_size": 0,
            },
            "top_symbols": [],
            "metadata": {},
        }

    def _discover_assets(self) -> List[str]:
        if not self.trading_client or GetAssetsRequest is None or AssetClass is None or AssetStatus is None:
            logger.warning("Universe snapshot builder: trading client unavailable for asset discovery")
            return []

        try:
            request = GetAssetsRequest(status=AssetStatus.ACTIVE, asset_class=AssetClass.US_EQUITY)
            assets = self.trading_client.get_all_assets(filter=request)
        except Exception as exc:
            logger.warning("Universe snapshot builder: asset discovery failed: %s", exc)
            return []

        symbols = []
        for asset in assets or []:
            symbol = str(getattr(asset, "symbol", "") or "").upper().strip()
            if not symbol:
                continue
            if not bool(getattr(asset, "tradable", False)):
                continue
            if not self._is_symbol_allowed(symbol):
                continue
            if self._is_obvious_fund_or_etf(asset):
                continue
            exchange = str(getattr(asset, "exchange", "") or "").upper()
            if "OTC" in exchange:
                continue
            symbols.append(symbol)

        symbols = sorted(set(symbols))
        logger.info("Universe snapshot builder: discovered %s active tradable equities", len(symbols))
        return symbols

    def _resolve_curated_symbols(self, curated_symbols: Optional[Sequence[str]]) -> List[str]:
        if curated_symbols is not None:
            return self._normalize_symbols(curated_symbols)

        raw_watchlist = getattr(TradingConfig, "WATCHLIST", {}) or {}
        if not isinstance(raw_watchlist, dict):
            return self._normalize_symbols(raw_watchlist)

        collected: List[str] = []
        for group_name, tickers in raw_watchlist.items():
            if str(group_name or "").upper() == "REGIME_ETFS":
                continue
            collected.extend(list(tickers or []))
        return self._normalize_symbols(collected)

    @staticmethod
    def _normalize_symbols(symbols: Sequence[str]) -> List[str]:
        seen = set()
        normalized: List[str] = []
        for raw in symbols or []:
            symbol = str(raw or "").upper().strip()
            if not symbol or symbol in seen:
                continue
            if not UniverseSnapshotBuilder._is_symbol_allowed(symbol):
                continue
            seen.add(symbol)
            normalized.append(symbol)
        return normalized

    def _apply_curated_overlay(self, discovered_assets: Sequence[str]) -> tuple[List[str], List[str]]:
        if not self._curated_symbols:
            return list(discovered_assets or []), []
        combined = list(discovered_assets or [])
        seen = set(combined)
        added: List[str] = []
        for symbol in self._curated_symbols:
            if symbol in seen:
                continue
            if not self._is_curated_overlay_eligible(symbol):
                continue
            combined.append(symbol)
            seen.add(symbol)
            added.append(symbol)
        return combined, added

    def _is_curated_overlay_eligible(self, symbol: str) -> bool:
        if not self.trading_client or not hasattr(self.trading_client, "get_asset"):
            return False

        try:
            asset = self.trading_client.get_asset(symbol)
        except Exception:
            return False

        if asset is None:
            return False
        if not bool(getattr(asset, "tradable", False)):
            return False
        if not self._is_symbol_allowed(symbol):
            return False
        if self._is_obvious_fund_or_etf(asset):
            return False

        exchange = str(getattr(asset, "exchange", "") or "").upper()
        if "OTC" in exchange:
            return False

        return True

    @staticmethod
    def _is_symbol_allowed(symbol: str) -> bool:
        return symbol.isalpha() and 1 <= len(symbol) <= 5

    @staticmethod
    def _is_obvious_fund_or_etf(asset) -> bool:
        name = str(getattr(asset, "name", "") or "").upper()
        blockers = (
            " ETF",
            " ETN",
            " TRUST",
            " FUND",
            " SHARES",
            " INDEX",
            " BOND",
            " PROSHARES",
            " ISHARES",
            " DIREXION",
            " SPDR",
            " INVESCO",
            " VANGUARD",
            " ARK ",
            " GLOBAL X",
            " FIRST TRUST",
            " WISDOMTREE",
            " SCHWAB",
            " VANECK",
            " CBOE",
        )
        return any(token in name for token in blockers)

    def _compute_features(self, symbol: str, bars: Sequence[Dict]) -> Optional[Dict]:
        if not bars or len(bars) < 20:
            return None

        closes = [float(b["close"]) for b in bars]
        highs = [float(b["high"]) for b in bars]
        lows = [float(b["low"]) for b in bars]
        volumes = [float(b["volume"]) for b in bars]

        current = closes[-1]
        if current <= 0:
            return None

        lookback20 = min(20, len(bars))
        lookback14 = min(14, len(bars))
        avg_volume_20 = sum(volumes[-lookback20:]) / lookback20
        avg_dollar_volume_20 = sum((closes[i] * volumes[i]) for i in range(len(bars) - lookback20, len(bars))) / lookback20
        avg_range_14 = sum((highs[i] - lows[i]) for i in range(len(bars) - lookback14, len(bars))) / lookback14
        atr_pct_14 = (avg_range_14 / current) * 100.0 if current > 0 else 0.0
        return_20d_pct = _safe_pct(current, closes[-21]) if len(closes) >= 21 else 0.0
        return_5d_pct = _safe_pct(current, closes[-6]) if len(closes) >= 6 else 0.0
        recent_volume_5 = sum(volumes[-5:]) / min(5, len(volumes))
        volume_ratio_5d = (recent_volume_5 / avg_volume_20) if avg_volume_20 > 0 else 0.0
        ma_20 = sum(closes[-lookback20:]) / lookback20
        lookback60 = min(60, len(bars))
        ma_60 = sum(closes[-lookback60:]) / lookback60
        close_vs_ma20_pct = _safe_pct(current, ma_20)
        close_vs_ma60_pct = _safe_pct(current, ma_60)
        high_20 = max(highs[-lookback20:])
        low_20 = min(lows[-lookback20:])
        high_60 = max(highs[-lookback60:])
        low_60 = min(lows[-lookback60:])
        dist_to_20d_high_pct = ((high_20 - current) / high_20) * 100.0 if high_20 > 0 else 0.0
        dist_to_20d_low_pct = ((current - low_20) / low_20) * 100.0 if low_20 > 0 else 0.0
        dist_to_60d_high_pct = ((high_60 - current) / high_60) * 100.0 if high_60 > 0 else 0.0
        dist_to_60d_low_pct = ((current - low_60) / low_60) * 100.0 if low_60 > 0 else 0.0
        return_60d_pct = _safe_pct(current, closes[-61]) if len(closes) >= 61 else return_20d_pct
        lookback10 = min(10, len(bars))
        high_10 = max(highs[-lookback10:])
        low_10 = min(lows[-lookback10:])
        range_pct_10 = ((high_10 - low_10) / current) * 100.0 if current > 0 else 0.0
        if high_10 > low_10:
            close_position_10 = (current - low_10) / (high_10 - low_10)
        else:
            close_position_10 = 0.5
        abs_moves_20 = [
            abs(closes[i] - closes[i - 1])
            for i in range(max(1, len(closes) - lookback20 + 1), len(closes))
        ]
        total_path_20 = sum(abs_moves_20)
        trend_efficiency_20 = (
            abs(current - closes[-lookback20]) / total_path_20
            if lookback20 > 1 and total_path_20 > 0
            else 0.0
        )

        liquidity = _log_norm(avg_dollar_volume_20, 6.7, 9.7)
        movement = _clip01((atr_pct_14 - 1.0) / 7.0)
        activity = _clip01((volume_ratio_5d - 1.0) / 1.5)
        abs_trend = _clip01(abs(return_20d_pct) / 20.0)
        bullish_trend = _clip01(return_20d_pct / 20.0)
        bullish_trend_60 = _clip01(return_60d_pct / 30.0)
        bearish_trend = _clip01((-return_20d_pct) / 20.0)
        bearish_trend_60 = _clip01((-return_60d_pct) / 30.0)
        breakout_proximity = _clip01((5.0 - dist_to_20d_high_pct) / 5.0)
        breakout_proximity_60 = _clip01((8.0 - dist_to_60d_high_pct) / 8.0)
        low_proximity = _clip01((5.0 - dist_to_20d_low_pct) / 5.0)
        low_proximity_60 = _clip01((8.0 - dist_to_60d_low_pct) / 8.0)
        below_ma20 = _clip01((-close_vs_ma20_pct) / 8.0)
        above_ma20 = _clip01(close_vs_ma20_pct / 8.0)
        below_ma60 = _clip01((-close_vs_ma60_pct) / 12.0)
        above_ma60 = _clip01(close_vs_ma60_pct / 12.0)
        range_tightness_10 = _clip01((18.0 - range_pct_10) / 18.0)
        top_of_range_10 = _clip01(close_position_10)
        bottom_of_range_10 = _clip01(1.0 - close_position_10)
        trend_quality_20 = _clip01(trend_efficiency_20)
        remora_liquidity_fit = 1.0 - _clip01(abs(math.log10(max(avg_dollar_volume_20, 1.0)) - 7.4) / 1.0)

        base_score = (
            0.45 * liquidity
            + 0.25 * movement
            + 0.15 * activity
            + 0.15 * abs_trend
        )
        voyager_score = (
            0.24 * liquidity
            + 0.22 * bullish_trend
            + 0.16 * bullish_trend_60
            + 0.12 * breakout_proximity_60
            + 0.10 * above_ma20
            + 0.08 * above_ma60
            + 0.08 * trend_quality_20
        )
        sniper_score = (
            0.18 * liquidity
            + 0.20 * movement
            + 0.18 * activity
            + 0.15 * _clip01(return_5d_pct / 8.0)
            + 0.12 * _clip01(return_20d_pct / 15.0)
            + 0.10 * breakout_proximity
            + 0.07 * top_of_range_10
        )
        short_score = (
            0.22 * liquidity
            + 0.20 * bearish_trend
            + 0.16 * bearish_trend_60
            + 0.14 * below_ma20
            + 0.10 * below_ma60
            + 0.10 * activity
            + 0.08 * low_proximity_60
        )
        remora_score = (
            0.28 * remora_liquidity_fit
            + 0.22 * movement
            + 0.20 * activity
            + 0.15 * _clip01(abs(return_5d_pct) / 8.0)
            + 0.08 * _clip01(atr_pct_14 / 6.0)
            + 0.07 * _clip01(abs(close_vs_ma20_pct) / 8.0)
        )
        contrarian_score = (
            0.18 * liquidity
            + 0.22 * bearish_trend
            + 0.14 * bearish_trend_60
            + 0.18 * activity
            + 0.14 * movement
            + 0.14 * low_proximity_60
        )

        return {
            "symbol": symbol,
            "price": current,
            "avg_volume_20": avg_volume_20,
            "avg_dollar_volume_20": avg_dollar_volume_20,
            "atr_pct_14": atr_pct_14,
            "return_20d_pct": return_20d_pct,
            "return_60d_pct": return_60d_pct,
            "return_5d_pct": return_5d_pct,
            "volume_ratio_5d": volume_ratio_5d,
            "close_vs_ma20_pct": close_vs_ma20_pct,
            "close_vs_ma60_pct": close_vs_ma60_pct,
            "dist_to_20d_high_pct": dist_to_20d_high_pct,
            "dist_to_20d_low_pct": dist_to_20d_low_pct,
            "dist_to_60d_high_pct": dist_to_60d_high_pct,
            "dist_to_60d_low_pct": dist_to_60d_low_pct,
            "range_pct_10": range_pct_10,
            "close_position_10": close_position_10,
            "trend_efficiency_20": trend_efficiency_20,
            "base_score": base_score,
            "voyager_score": voyager_score,
            "sniper_score": sniper_score,
            "short_score": short_score,
            "remora_score": remora_score,
            "contrarian_score": contrarian_score,
        }

    def _passes_basic_filters(self, row: Dict) -> bool:
        price = float(row["price"])
        avg_volume = float(row["avg_volume_20"])
        avg_dollar_volume = float(row["avg_dollar_volume_20"])
        return (
            self.MIN_PRICE <= price <= self.MAX_PRICE
            and avg_volume >= self.MIN_AVG_VOLUME
            and avg_dollar_volume >= self.MIN_AVG_DOLLAR_VOLUME
        )

    @staticmethod
    def _voyager_pullback_ready(row: Dict) -> bool:
        """
        Voyager should start from leaders that have reset enough for an
        accumulation-style entry, not names already hugging fresh highs.

        We use existing medium-term structure metrics instead of adding another
        long-horizon feature: distance from the 60d high plus distance from the
        60d trend baseline. This keeps the filter cheap and aligned with the
        later accumulation-zone logic.
        """
        return (
            4.0 <= row["dist_to_60d_high_pct"] <= 35.0
            and row["close_vs_ma60_pct"] <= 18.0
            and row["close_vs_ma20_pct"] >= -4.0
        )

    @staticmethod
    def _voyager_filter(row: Dict) -> bool:
        price = row["price"]
        if price < 10:
            return False
        if not UniverseSnapshotBuilder._voyager_pullback_ready(row):
            return False
        # Standard path: established uptrend over 20d and 60d
        standard = (
            row["return_20d_pct"] >= 0
            and row["return_60d_pct"] >= -2
            and row["close_vs_ma20_pct"] >= -1
            and row["close_vs_ma60_pct"] >= -3
            and row["dist_to_60d_high_pct"] <= 20
            and row["trend_efficiency_20"] >= 0.2
        )
        # Recovery path: stock was beaten down over 60d but is now recovering
        # strongly on both 20d and 5d — captures V-shaped bounces and
        # macro-driven recovery plays that the standard path misses.
        recovery = (
            row["return_20d_pct"] >= 8.0
            and row["return_5d_pct"] >= 3.0
            and row["close_vs_ma20_pct"] >= 0
        )
        return standard or recovery

    @staticmethod
    def _sniper_filter(row: Dict) -> bool:
        return (
            row["price"] >= 10
            and row["atr_pct_14"] >= 1.5
            and row["return_20d_pct"] >= 2
            and row["close_vs_ma20_pct"] >= 0
            and row["dist_to_20d_high_pct"] <= 8
            and row["close_position_10"] >= 0.65
            and row["volume_ratio_5d"] >= 0.75
            and row["range_pct_10"] <= 18
        )

    @staticmethod
    def _short_filter(row: Dict) -> bool:
        return (
            row["price"] >= 8
            and row["return_20d_pct"] <= -2
            and row["close_vs_ma20_pct"] <= -1
            and row["dist_to_60d_low_pct"] <= 12
            and row["close_position_10"] <= 0.35
            and row["volume_ratio_5d"] >= 0.8
        )

    @staticmethod
    def _remora_filter(row: Dict) -> bool:
        return (
            8 <= row["price"] <= 200
            and 8_000_000 <= row["avg_dollar_volume_20"] <= 200_000_000
            and row["atr_pct_14"] >= 1.8
            and abs(row["return_5d_pct"]) >= 1.5
            and row["volume_ratio_5d"] >= 1.0
        )

    @staticmethod
    def _contrarian_filter(row: Dict) -> bool:
        return (
            row["price"] >= 5
            and (row["return_20d_pct"] <= -8 or row["dist_to_60d_low_pct"] <= 5)
            and row["volume_ratio_5d"] >= 1.0
            and row["atr_pct_14"] >= 1.8
        )

    @staticmethod
    def _route_symbols(base_rows: Sequence[Dict], score_key: str, limit: int, predicate) -> List[str]:
        filtered = [row for row in base_rows if predicate(row)]
        filtered.sort(key=lambda row: (-row[score_key], -row["base_score"], row["symbol"]))
        return [row["symbol"] for row in filtered[: max(0, int(limit))]]

    def _persist_snapshot(self, snapshot: Dict) -> None:
        try:
            os.makedirs(self.reports_dir, exist_ok=True)
            latest_path = os.path.join(self.reports_dir, "universe_snapshot_latest.json")
            dated_name = f"universe_snapshot_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
            dated_path = os.path.join(self.reports_dir, dated_name)

            for path in (latest_path, dated_path):
                with tempfile.NamedTemporaryFile("w", dir=self.reports_dir, delete=False, encoding="utf-8") as tmp:
                    json.dump(snapshot, tmp, indent=2, sort_keys=True)
                    tmp.flush()
                    os.fsync(tmp.fileno())
                os.replace(tmp.name, path)
        except Exception as exc:
            logger.warning("Universe snapshot builder: could not persist snapshot: %s", exc)
