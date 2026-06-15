"""
research/scanner_truth/dataio.py — read-only data access for the autopsy.

All reads are cache-only: price parquet under cache/prices, profile JSON from
cache_meta (read-only SELECT), and the operational DB tables decisions /
veto_log / scan_results / paper_signals / voyager_paper_signals (read-only).
Nothing here writes, mutates, or calls a provider.
"""
from __future__ import annotations

import json
import sqlite3
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

REPO = Path(__file__).resolve().parents[2]
PRICES_DIR = REPO / "cache" / "prices"
# Phase 1G.7: a SEPARATE deep-history price cache. The production daemon
# overwrites cache/prices/*.parquet with only ~90 day windows each scan, so any
# deep history written there is clobbered. The deepening refresh
# (scripts/deepen_price_cache.py) writes merge-on-write parquets here instead,
# and load_prices prefers them when present (additive: a no-op until populated).
DEEP_PRICES_DIR = REPO / "cache" / "prices_deep"
DB_PATH = REPO / "db" / "trading.db"
RESEARCH_CACHE = REPO / "cache" / "research"
HISTORY_DIR = REPO / "data" / "research"
LOGS_DIR = REPO / "logs"

BENCHMARKS = ("SPY", "QQQ")


def _ro_conn() -> sqlite3.Connection:
    """Read-only connection (immutable) — guarantees the autopsy cannot mutate."""
    return sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)


@lru_cache(maxsize=1)
def load_profiles() -> Dict[str, Dict]:
    """ticker → {sector, industry, marketCap, companyName} from cache_meta
    fmp:profile payloads. The only sector/market-cap source available."""
    out: Dict[str, Dict] = {}
    with _ro_conn() as con:
        for key, payload in con.execute(
            "SELECT key, payload FROM cache_meta WHERE key LIKE 'fmp:profile:%'"
        ):
            try:
                d = json.loads(payload)
            except Exception:
                continue
            t = key.split(":")[-1].upper()
            out[t] = {
                "sector": d.get("sector"),
                "industry": d.get("industry"),
                "market_cap": d.get("marketCap"),
                "company_name": d.get("companyName"),
            }
    return out


def _read_parquet(p: Path) -> Optional[pd.DataFrame]:
    if not p.exists():
        return None
    try:
        df = pd.read_parquet(p)
    except Exception:
        return None
    if df.empty or "close" not in df.columns:
        return None
    df = df.sort_index()
    df.index = pd.to_datetime(df.index)
    return df


def load_prices(ticker: str, prefer_deep: bool = True) -> Optional[pd.DataFrame]:
    """Read a ticker's OHLCV parquet. When ``prefer_deep`` (default), the deep
    cache (cache/prices_deep) is used if it exists and is at least as long as
    the shallow cache; otherwise falls back to cache/prices. Additive: until the
    deepening refresh populates prices_deep, this is identical to the old path."""
    t = ticker.upper()
    shallow = _read_parquet(PRICES_DIR / f"{t}.parquet")
    if prefer_deep:
        deep = _read_parquet(DEEP_PRICES_DIR / f"{t}.parquet")
        if deep is not None and (shallow is None or len(deep) >= len(shallow)):
            return deep
    return shallow


def deep_bar_count(ticker: str) -> int:
    df = _read_parquet(DEEP_PRICES_DIR / f"{ticker.upper()}.parquet")
    return 0 if df is None else int(df["close"].notna().sum())


def all_price_tickers() -> List[str]:
    """Research universe = every ticker with a shallow parquet, unioned with any
    deep-only parquets so deepened-but-not-yet-scanned names are still visible."""
    shallow = {p.stem.upper() for p in PRICES_DIR.glob("*.parquet")}
    deep = {p.stem.upper() for p in DEEP_PRICES_DIR.glob("*.parquet")} \
        if DEEP_PRICES_DIR.exists() else set()
    return sorted(shallow | deep)


@lru_cache(maxsize=4)
def benchmark_calendar() -> pd.DatetimeIndex:
    """Canonical trading calendar = SPY's bar index. All windows are measured
    in SPY trading days so 'last N trading days' is consistent across tickers."""
    spy = load_prices("SPY")
    if spy is None:
        raise RuntimeError("SPY benchmark parquet missing — cannot define calendar")
    return spy.index


# ── Theme classification (sector/industry/name keyword map) ──────────────────
# Keyword → theme, FIRST MATCH WINS (order matters). Themes are derived from the
# profile text (sector/industry/company_name), not hand-assigned per ticker, so
# the mapping is auditable. LIMITATION: the FMP industry taxonomy is coarse —
# memory and AI-hardware names mostly read "Semiconductors" or "Hardware,
# Equipment & Parts" and cannot be cleanly separated from logic semis by
# profile alone. We catch the well-known memory/storage names by company name
# and otherwise fold them into `semiconductors` / `hardware`. This is a
# documented limitation, not a silent fudge; a future Theme Leadership Radar
# (Task 6) would cluster by price co-movement instead.
_MEMORY_NAMES = ("sandisk", "seagate", "western digital", "micron", "kioxia", "netlist")
_THEME_RULES = [
    ("quantum", ("quantum",)),
    ("space_aerospace", ("aerospace", "space", "satellite", "launch", "rocket", "defense")),
    ("nuclear_energy", ("uranium", "nuclear", "reactor", "fusion", "renewable util",
                        "regulated electric", "independent power")),
    ("crypto_blockchain", ("crypto", "bitcoin", "blockchain", "digital asset")),
    ("memory_storage", ("data storage", "memory")),  # name-based handled below
    ("semiconductors", ("semiconductor",)),
    ("hardware", ("computer hardware", "hardware, equipment")),
    ("biotech_healthcare", ("biotech", "pharmaceutic", "therapeutic", "biolog", "drug",
                            "healthcare", "medical", "diagnostic", "gene ")),
]


def classify_theme(profile: Optional[Dict]) -> str:
    if not profile:
        return "unknown"
    name = str(profile.get("company_name") or "").lower()
    if any(m in name for m in _MEMORY_NAMES):
        return "memory_storage"
    hay = " ".join(
        str(profile.get(k) or "") for k in ("sector", "industry", "company_name")
    ).lower()
    if not hay.strip():
        return "unknown"
    for theme, kws in _THEME_RULES:
        if any(kw in hay for kw in kws):
            return theme
    return "other"


def rel_to_repo(path: Path) -> str:
    """Repo-relative string for display, robust to paths outside the repo (e.g.
    when a test monkeypatches an output path into a tmp dir)."""
    try:
        return str(Path(path).relative_to(REPO))
    except Exception:
        return str(path)


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, default=str))


def write_text(path: Path, lines: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")


def append_jsonl(path: Path, rows: List[Dict]) -> int:
    """Append one JSON object per line. Returns rows written. Append-only — never
    rewrites or mutates prior lines (forward evidence is immutable history)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        for r in rows:
            fh.write(json.dumps(r, default=str) + "\n")
    return len(rows)


def read_jsonl(path: Path) -> List[Dict]:
    if not path.exists():
        return []
    out: List[Dict] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out
