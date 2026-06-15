import csv
import json
import os
from dataclasses import dataclass
from typing import Dict, Tuple


@dataclass(frozen=True)
class SectorResolution:
    ticker: str
    sector_etf: str
    resolver_confidence: float
    source: str
    notes: str = ""


# Local explicit map for the 68-ticker universe.
SECTOR_ETF_MAP: Dict[str, str] = {
    "NVDA": "XLK",
    "AMD": "XLK",
    "MSFT": "XLK",
    "GOOG": "XLK",
    "AAPL": "XLK",
    "META": "XLK",
    "AMZN": "XLY",
    "PLTR": "XLK",
    "NET": "IGV",
    "CRM": "IGV",
    "NOW": "IGV",
    "CRDO": "XLK",
    "NVTS": "XLK",
    "SYM": "XLK",
    "ZETA": "IGV",
    "TQQQ": "XLK",
    "TSLA": "XLY",
    "SERV": "XLK",
    "BMNR": "XLK",
    "NBIS": "XLK",
    "BULL": "XLK",
    "VG": "XLK",
    "SOXL": "XLK",
    "RIVN": "XLY",
    "MU": "XLK",
    "HOOD": "XLF",
    "SOFI": "XLF",
    "PYPL": "XLF",
    "RKT": "XLF",
    "SEZL": "XLF",
    "SHOP": "XLY",
    "IREN": "XLF",
    "APLD": "XLF",
    "COIN": "XLF",
    "IBIT": "XLF",
    "CIFR": "XLF",
    "HUT": "XLF",
    "OKLO": "XLI",
    "EOSE": "XLI",
    "TAC": "XLI",
    "PWR": "XLI",
    "CRCL": "XLI",
    "RKLB": "XLI",
    "POET": "XLI",
    "ASTS": "XLI",
    "SATL": "XLI",
    "HIMS": "XLV",
    "OSCR": "XLV",
    "UNH": "XLV",
    "NKE": "XLY",
    "HD": "XLY",
    "SFM": "XLY",
    "BROS": "XLY",
    "CAVA": "XLY",
    "RCL": "XLY",
    "UAL": "XLY",
    "NFLX": "XLY",
    "GRAB": "XLY",
    "TSM": "XLK",
    "BABA": "XLK",
    "BITU": "XLF",
    "XLF": "XLF",
    "XLI": "XLI",
    "XLK": "XLK",
    "IWM": "IWM",
    "SPY": "SPY",
    "VXX": "VXX",
    "IGV": "IGV",
    "RDW": "XLI",
}
LOCAL_MAP = SECTOR_ETF_MAP


class SectorResolver:
    def __init__(self, overrides_path: str = "sector_overrides.json", universe_csv_path: str = "universe.csv"):
        self.overrides_path = overrides_path
        self.universe_csv_path = universe_csv_path
        self._overrides = self._load_json(overrides_path) or {}
        self._universe = self._load_universe(universe_csv_path)

    def _load_json(self, path):
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        return None

    def _load_universe(self, path) -> Dict[str, Dict]:
        data = {}
        if not os.path.exists(path):
            return data
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                ticker = (row.get("ticker") or "").upper().strip()
                if not ticker:
                    continue
                data[ticker] = {
                    k: (v.strip() if isinstance(v, str) else v)
                    for k, v in row.items()
                }
        return data

    def resolve(self, ticker: str) -> SectorResolution:
        t = (ticker or "").upper().strip()
        if not t:
            return SectorResolution(
                ticker=t,
                sector_etf="SPY",
                resolver_confidence=0.0,
                source="fallback",
                notes="empty_ticker",
            )

        # 1) explicit overrides win
        override = self._overrides.get(t)
        if override and override.get("sector_etf"):
            etf = str(override["sector_etf"]).upper().strip()
            note = str(override.get("note") or "override")
            return SectorResolution(
                ticker=t,
                sector_etf=etf,
                resolver_confidence=1.0,
                source="manual",
                notes=note,
            )

        # 2) hardcoded local map
        etf = SECTOR_ETF_MAP.get(t)
        if etf:
            return SectorResolution(
                ticker=t,
                sector_etf=etf,
                resolver_confidence=1.0,
                source="manual",
                notes="local_68_map",
            )

        # 3) universe CSV (lower confidence than audited map)
        row = self._universe.get(t)
        if row:
            sector_etf = (row.get("sector_etf") or "").upper().strip()
            if sector_etf:
                return SectorResolution(
                    ticker=t,
                    sector_etf=sector_etf,
                    resolver_confidence=0.8,
                    source="csv",
                    notes="universe_csv",
                )

        # fallback should never drive hard veto logic.
        return SectorResolution(
            ticker=t,
            sector_etf="SPY",
            resolver_confidence=0.35,
            source="fallback",
            notes="unmapped_default_spy",
        )

    def resolve_sector_etf(self, ticker: str) -> Tuple[str, Dict]:
        """Compatibility helper: returns (sector_etf, meta)."""
        res = self.resolve(ticker)
        return res.sector_etf, {
            "resolver_confidence": res.resolver_confidence,
            "source": res.source,
            "notes": res.notes,
            "ticker": res.ticker,
        }


def resolve_sector_etf(ticker: str) -> Dict:
    """Functional entrypoint returning dict payload for analytics and agents."""
    res = SectorResolver().resolve(ticker)
    is_local = res.source in {"manual", "csv"} and res.sector_etf != "SPY"
    return {
        "ok": bool(is_local),
        "ticker": res.ticker,
        "sector_etf": (res.sector_etf if is_local else None),
        "resolver_confidence": (float(res.resolver_confidence or 0.0) if is_local else 0.0),
        "source": ("local_map" if res.source == "manual" else res.source),
        "reason": ("local_map" if res.source == "manual" else "missing_mapping"),
        "notes": res.notes,
        "fallback_sector_etf": res.sector_etf,
    }
