#!/usr/bin/env python3
"""
Smart Social Arb Radar V1.

Research-only, twice-weekly side module for finding a small number of
market-relevant news/theme/ticker leads worth manual review.

Explicit non-goals:
  - not a trade engine
  - not paper evidence
  - not sleeve approval
  - not Alpha Discovery scoring
  - not an auto-trading feature

Dashboard integration must read only cache/research/social_arb_latest.json.
This script is the only place provider calls are allowed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sqlite3
import sys
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - optional runtime convenience
    load_dotenv = None

if load_dotenv is not None and os.getenv("GEM_TRADER_SKIP_DOTENV", "").lower() not in {"1", "true", "yes"}:
    env_path = os.getenv("SNIPER_ENV_PATH", "").strip()
    if env_path:
        load_dotenv(env_path, override=True)
    load_dotenv(ROOT / ".env", override=True)

# Missing-source fallback: allow compile/smoke runs without credentials. Real
# provider calls still fail gracefully if these placeholders are the only values.
os.environ.setdefault("ALPACA_API_KEY", "offline")
os.environ.setdefault("ALPACA_SECRET_KEY", "offline")
os.environ.setdefault("FMP_API_KEY", "offline")
os.environ.setdefault("FMP_BASE_URL", "https://financialmodelingprep.com/stable")
os.environ.setdefault("ALPACA_PAPER", "true")
os.environ.setdefault("PAPER_TRADING", "true")
os.environ.setdefault("DB_PATH", str(ROOT / "db" / "trading.db"))
os.environ.setdefault("CACHE_DIR", str(ROOT / "cache"))
os.environ.setdefault("LOG_DIR", str(ROOT / "logs"))

import requests

import core.config as cfg
from core.fmp_client import get_fmp

try:
    import yfinance as yf
except Exception:  # pragma: no cover - optional dependency
    yf = None


VERSION = "SMART_SOCIAL_ARB_RADAR_V1"
RESEARCH_DIR = cfg.CACHE_DIR / "research"
SOCIAL_JSON_PATH = RESEARCH_DIR / "social_arb_latest.json"
SOCIAL_RAW_PATH = RESEARCH_DIR / "social_arb_raw_latest.json"
SOCIAL_TEXT_PATH = cfg.LOG_DIR / "social_arb_latest.txt"

DEFAULT_REVIEW_LIMIT = 10
DEFAULT_VISIBLE_LIMIT = 8
DEFAULT_MIN_RUN_INTERVAL_HOURS = 10.0

KNOWN_INSTRUMENTS = {
    "SPY", "QQQ", "IWM", "VXX", "GLD", "TLT", "HYG", "SQQQ", "QID", "TQQQ",
    "QLD", "SOXL", "SOXS", "SPXU", "UPRO", "SDS", "SH", "DOG", "DXD", "TZA",
    "TNA", "USO", "SLV", "XLF", "XLE", "XLI", "XLK", "XBI", "ARKK",
}

COMMON_FALSE_TICKERS = {
    "A", "AI", "ALL", "AM", "AND", "API", "ARE", "AS", "AT", "BE", "CEO",
    "CFO", "CPI", "DIY", "FDA", "FOMC", "GDP", "IPO", "IT", "JOB", "MAY",
    "NEW", "NO", "ON", "OR", "PM", "PR", "Q", "QOQ", "SEC", "THE", "US",
    "USA", "VS", "YOY",
}

# Tickers that are also common legal-entity suffixes (AG=Aktiengesellschaft,
# SA=Société Anonyme, SE=Societas Europaea, NV=Naamloze Vennootschap,
# PLC=Public Limited Company).  A bare uppercase token in news text is
# almost always the suffix, not the stock.  Mapping survives only when there
# is at least one of: a $-prefixed mention ("$AG"), a validated company-name
# alias from COMPANY_ALIASES, or an explicit FMP subject tag.
CORPORATE_SUFFIX_TICKERS = frozenset({"AG", "SA", "SE", "NV", "PLC"})

FALLBACK_SYMBOLS = {
    "AAPL", "MSFT", "NVDA", "GOOGL", "GOOG", "AMZN", "META", "TSLA", "AMD",
    "AVGO", "SMCI", "VRT", "ORCL", "CRM", "NOW", "SNOW", "PLTR", "CRWD",
    "PANW", "NET", "DDOG", "SHOP", "TTD", "NFLX", "SPOT", "DIS", "ROKU",
    "COIN", "MSTR", "MARA", "RIOT", "HOOD", "SOFI", "JPM", "BAC", "GS",
    "MS", "V", "MA", "PYPL", "XOM", "CVX", "OXY", "HAL", "SLB",
    "CCJ", "CEG", "VST", "TLN", "OKLO", "SMR", "NNE", "GEV", "LMT", "RTX",
    "NOC", "BA", "KTOS", "RCAT", "AXON", "LLY", "NVO", "UNH", "HIMS",
    "MRNA", "PFE", "REGN", "VRTX", "GILD", "ISRG", "WMT", "COST", "TGT",
    "HD", "MCD", "CAVA", "RDDT", "SNAP", "DASH", "UBER", "ABNB", "RIVN",
    "LCID", "F", "GM", "CAT", "DE", "NEM", "FCX", "MP", "UUUU", "URNM",
}

COMPANY_ALIASES = {
    "AAPL": ["apple"],
    "MSFT": ["microsoft"],
    "NVDA": ["nvidia"],
    "GOOGL": ["alphabet", "google"],
    "GOOG": ["alphabet", "google"],
    "AMZN": ["amazon", "aws"],
    "META": ["meta", "facebook", "instagram"],
    "TSLA": ["tesla"],
    "AMD": ["advanced micro devices"],
    "AVGO": ["broadcom"],
    "SMCI": ["super micro", "supermicro"],
    "VRT": ["vertiv"],
    "ORCL": ["oracle"],
    "PLTR": ["palantir"],
    "CRWD": ["crowdstrike"],
    "PANW": ["palo alto networks"],
    "NET": ["cloudflare"],
    "DDOG": ["datadog"],
    "SHOP": ["shopify"],
    "TTD": ["trade desk"],
    "NFLX": ["netflix"],
    "SPOT": ["spotify"],
    "DIS": ["disney"],
    "COIN": ["coinbase"],
    "MSTR": ["microstrategy", "strategy"],
    "MARA": ["marathon digital"],
    "RIOT": ["riot platforms"],
    "HOOD": ["robinhood"],
    "JPM": ["jpmorgan", "jp morgan"],
    "BAC": ["bank of america"],
    "GS": ["goldman"],
    "MS": ["morgan stanley"],
    "XOM": ["exxon"],
    "CVX": ["chevron"],
    "OXY": ["occidental"],
    "CCJ": ["cameco"],
    "CEG": ["constellation energy"],
    "VST": ["vistra"],
    "TLN": ["talen energy"],
    "OKLO": ["oklo"],
    "SMR": ["nuscale"],
    "NNE": ["nano nuclear"],
    "GEV": ["ge vernova"],
    "LMT": ["lockheed"],
    "RTX": ["rtx", "raytheon"],
    "NOC": ["northrop"],
    "BA": ["boeing"],
    "AXON": ["axon"],
    "LLY": ["eli lilly", "lilly"],
    "NVO": ["novo nordisk"],
    "UNH": ["unitedhealth", "united health"],
    "HIMS": ["hims", "hims & hers"],
    "MRNA": ["moderna"],
    "PFE": ["pfizer"],
    "REGN": ["regeneron"],
    "VRTX": ["vertex"],
    "GILD": ["gilead"],
    "ISRG": ["intuitive surgical"],
    "WMT": ["walmart"],
    "COST": ["costco"],
    "TGT": ["target"],
    "HD": ["home depot"],
    "MCD": ["mcdonald"],
    "CAVA": ["cava"],
    "RDDT": ["reddit"],
    "SNAP": ["snap"],
    "DASH": ["doordash"],
    "UBER": ["uber"],
    "ABNB": ["airbnb"],
    "RIVN": ["rivian"],
    "LCID": ["lucid"],
    "CAT": ["caterpillar"],
    "DE": ["deere"],
    "NEM": ["newmont"],
    "FCX": ["freeport"],
    "MP": ["mp materials"],
    "TTMI": ["ttm technologies"],
    "AG": ["first majestic", "first majestic silver"],
}

THEME_RULES: Dict[str, Dict[str, Any]] = {
    "AI/Data Center": {
        "terms": ["artificial intelligence", " ai ", "gpu", "data center", "datacenter", "accelerator", "server"],
        "tickers": ["NVDA", "AMD", "AVGO", "SMCI", "VRT", "ORCL"],
        "novelty": 4.0,
    },
    "Semiconductors": {
        "terms": ["semiconductor", "chip", "foundry", "hbm", "memory chip", "wafer"],
        "tickers": ["NVDA", "AMD", "AVGO", "TSM", "MU", "AMAT", "LRCX"],
        "novelty": 5.0,
    },
    "Power/Nuclear": {
        "terms": ["nuclear", "uranium", "grid", "power demand", "small modular reactor", "smr", "electricity"],
        "tickers": ["CCJ", "CEG", "VST", "TLN", "OKLO", "SMR", "GEV"],
        "novelty": 7.0,
    },
    "Crypto": {
        "terms": ["bitcoin", "crypto", "stablecoin", "ethereum", "digital asset"],
        "tickers": ["COIN", "MSTR", "MARA", "RIOT", "HOOD"],
        "novelty": 5.0,
    },
    "Defense": {
        "terms": ["defense", "missile", "drone", "aerospace", "pentagon", "dod contract", "geopolitical"],
        "tickers": ["LMT", "RTX", "NOC", "BA", "KTOS", "RCAT", "AXON"],
        "novelty": 6.0,
    },
    "Obesity/Healthcare": {
        "terms": ["glp-1", "obesity", "weight-loss", "weight loss", "medicare", "drug trial", "fda"],
        "tickers": ["LLY", "NVO", "HIMS", "UNH", "MRNA", "PFE"],
        "novelty": 5.0,
    },
    "Cybersecurity": {
        "terms": ["cybersecurity", "breach", "ransomware", "zero trust", "cloud security"],
        "tickers": ["CRWD", "PANW", "NET", "ZS", "FTNT"],
        "novelty": 5.0,
    },
    "Consumer/App Demand": {
        "terms": ["app downloads", "streaming", "e-commerce", "delivery", "advertising demand", "consumer demand"],
        "tickers": ["AMZN", "SHOP", "DASH", "UBER", "ABNB", "RDDT", "SNAP"],
        "novelty": 5.0,
    },
    "Macro/Policy": {
        "terms": ["tariff", "rate cut", "inflation", "fed", "fomc", "treasury yield", "export control"],
        "tickers": [],
        "novelty": 3.0,
    },
}

CATALYST_TERMS = {
    "earnings", "guidance", "raise", "cut", "beat", "miss", "forecast", "upgrade",
    "downgrade", "contract", "order", "award", "partnership", "launch", "approval",
    "fda", "trial", "merger", "acquisition", "buyout", "stake", "activist",
    "tariff", "export", "investigation", "lawsuit", "settlement", "recall",
    "bankruptcy", "default", "halts", "halted", "surge", "plunge", "rally",
}

GENERIC_NOISE_TERMS = {
    "stock market today", "stocks mixed", "dow futures", "nasdaq futures",
    "what to watch", "market wrap", "closing bell", "before the bell",
}

# Clickbait / listicle headline patterns. Detected by regex against the
# lowercased title so we kill the "X% Over a Decade", "Forget X. Buy Y",
# "Should You Buy", "5 Best Stocks" sponsored-content style content that
# is the dominant noise tier on FMP/News-API news feeds. Kept narrow on
# purpose — generic terms ("buy", "hold") never trigger by themselves.
LISTICLE_NOISE_PATTERNS = (
    r"\bover (?:a|\d+)\s+decade(?:s)?\b",
    r"\b\d+(?:,\d+)?(?:\.\d+)?\s*%\s+(?:over|in|gain|return)\b",
    r"\bforget\b[^.]{0,30}\b(?:buy|own|consider)\b",
    r"\b(?:reason matters more than)\b",
    r"\b(?:should you (?:buy|sell|own|consider))\b",
    r"\b(?:\d+\s+(?:best|top|reasons?|stocks?\s+to\s+(?:buy|own|watch)))\b",
    r"\b(?:will\s+(?:add\s+)?momentum|spark a rebound|recovery has faltered)\b",
    r"\b(?:mag\s*7|magnificent\s*7|fab(?:ulous)?\s*5)\b",
    r"\b(?:this is the\s+\w+\s+stock you|the\s+\w+\s+stock you\s+should\s+own)\b",
)

MEME_NOISE_TERMS = {
    "meme stock", "short squeeze", "squeeze", "penny stock", "pump", "moon",
    "reddit traders", "wallstreetbets",
}


@dataclass
class NormalizedItem:
    item_id: str
    title: str
    source: str
    source_type: str
    timestamp: str
    freshness_hours: float
    url: str
    tickers_mentioned: List[str]
    company_names: List[str]
    theme: str
    source_payload_ref: str
    # Per-ticker mapping provenance — keyed by ticker symbol. Each value carries
    # the location-aware evidence used to score the mapping (see normalize_items):
    #   in_title / in_body  — non-ambiguous evidence (ticker token or clean alias)
    #   is_subject          — ticker equals the FMP article `symbol` tag
    #   direct_symbol       — a bare/$-prefixed ticker token matched (vs. a name)
    #   ambiguous_hit       — only a generic-word alias (AMBIGUOUS_ALIAS_TERMS) hit
    #   alias               — the clean alias matched (for company_names display)
    ticker_evidence: Dict[str, Dict[str, Any]] = field(default_factory=dict)


@dataclass
class TickerMapping:
    ticker: str
    confidence: float
    method: str
    label: str
    in_title: bool = False
    is_subject: bool = False


@dataclass
class Candidate:
    id: str
    ticker: str
    bucket: str
    confidence: str
    confidence_score: float
    noise_risk: str
    source_type: str
    theme: str
    news_label: str
    why_it_matters: str
    manual_check_needed: str
    cross_refs: List[str]
    deterministic_score: float
    anthropic_verdict: str
    evidence_supporting: List[str]
    evidence_missing: List[str]
    source_count: int
    source_titles: List[str]
    urls: List[str]
    mapping_method: str
    mapping_confidence: float
    freshness_hours: float
    tape: Dict[str, Any]
    options: Dict[str, Any]
    thirteen_f: Dict[str, Any]
    score_blocks: Dict[str, float]
    noise_reasons: List[str]
    trend_signal: Dict[str, Any] = field(default_factory=dict)
    source_types: List[str] = field(default_factory=list)
    non_trends_source_count: int = 0
    has_google_trends: bool = False
    trend_z: Optional[float] = None
    # Hard contract: Google Trends is research-only corroboration. It cannot
    # create standalone leads, generate trade signals, or override tape/entry
    # quality. Consumers reading the artifact can rely on this invariant.
    trend_is_corrob_only: bool = True
    # Claude enrichment fields (populated by apply_reviews; UNKNOWN/None if not called)
    time_sensitivity: str = "UNKNOWN"   # BREAKING | RECENT | DATED | EXPIRED | UNKNOWN
    confidence_pct: Optional[int] = None  # 0-100: Claude's confidence a human review is worthwhile

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw[:19], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _age_hours(dt: Optional[datetime]) -> float:
    if dt is None:
        return 9999.0
    return max(0.0, (_utc_now() - dt).total_seconds() / 3600.0)


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        if isinstance(value, float) and math.isnan(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _clip(value: Any, width: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= width:
        return text
    return text[: max(0, width - 3)].rstrip() + "..."


def _stable_id(*parts: Any) -> str:
    joined = "|".join(str(p or "") for p in parts)
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()[:12]


def _domain(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
        return host.replace("www.", "")
    except Exception:
        return ""


def _tokens(text: str) -> List[str]:
    return [t for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) > 2]


def _story_key(title: str, theme: str) -> str:
    toks = [t for t in _tokens(title) if t not in {"the", "and", "for", "with", "from", "that", "this"}]
    return _stable_id(theme, " ".join(toks[:12]))


_LISTICLE_NOISE_RE = re.compile("|".join(LISTICLE_NOISE_PATTERNS), re.IGNORECASE)


def _is_generic_noise(title: str) -> bool:
    low = title.lower()
    if any(term in low for term in GENERIC_NOISE_TERMS):
        return True
    if _LISTICLE_NOISE_RE.search(low):
        return True
    token_count = len(_tokens(title))
    return token_count < 4


def _is_market_relevant(title: str, theme: str) -> bool:
    low = f" {title.lower()} "
    if theme and theme != "Unclassified":
        return True
    return any(term in low for term in CATALYST_TERMS)


def _theme_for_text(title: str, description: str = "") -> str:
    low = f" {(title or '').lower()} {(description or '').lower()} "
    best = "Unclassified"
    best_hits = 0
    for theme, rule in THEME_RULES.items():
        hits = sum(1 for term in rule["terms"] if term in low)
        if hits > best_hits:
            best = theme
            best_hits = hits
    return best


def _source_credibility(source_type: str, source: str, url: str) -> float:
    dom = _domain(url)
    if source_type == "fmp_stock_news":
        return 12.0
    if source_type == "news_api":
        credible_domains = {
            "reuters.com", "bloomberg.com", "wsj.com", "cnbc.com", "marketwatch.com",
            "finance.yahoo.com", "investors.com", "barrons.com", "seekingalpha.com",
        }
        if dom in credible_domains or any(dom.endswith("." + d) for d in credible_domains):
            return 12.0
        if source:
            return 9.0
    if source_type == "google_trends":
        # Unofficial, no narrative — only weight as a corroborating signal,
        # never the primary thesis. Cross-confirmation with a real news row
        # is what lifts a Trends-only ticker into XCONF/NEWS territory.
        return 5.0
    return 7.0


def _load_json(path: Path) -> Dict[str, Any]:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _load_universe_snapshot(path: Optional[Path] = None) -> Dict[str, Any]:
    return _load_json(path or (cfg.CACHE_DIR / "universe" / "universe_snapshot_latest.json"))


def _known_symbols(snapshot: Dict[str, Any]) -> set[str]:
    out = set(FALLBACK_SYMBOLS)
    for key in ("base_universe", "sniper_universe", "voyager_universe", "short_universe", "remora_universe", "contrarian_universe"):
        for sym in snapshot.get(key) or []:
            if sym:
                out.add(str(sym).upper())
    for sym in (snapshot.get("metadata") or {}).keys():
        out.add(str(sym).upper())
    for row in snapshot.get("strategy_candidates") or []:
        sym = str(row.get("symbol") or "").upper()
        if sym:
            out.add(sym)
    for artifact in (
        cfg.CACHE_DIR / "research" / "alpha_discovery_board_latest.json",
        cfg.CACHE_DIR / "research" / "alpha_discovery_overlay_latest.json",
    ):
        data = _load_json(artifact)
        for row in data.get("items") or []:
            sym = str(row.get("ticker") or "").upper()
            if sym:
                out.add(sym)
    return {s for s in out if s and s not in COMMON_FALSE_TICKERS}


def _scanner_symbols() -> set[str]:
    out: set[str] = set()
    try:
        con = sqlite3.connect(str(cfg.DB_PATH), timeout=3)
        for (ticker,) in con.execute(
            "SELECT DISTINCT ticker FROM scan_results "
            "WHERE ts > datetime('now', '-48 hours') "
            "AND status IN ('READY_NOW','SCAN_APPROVED','APPROVED','WATCH','GATED','ALLOCATION_BLOCKED')"
        ).fetchall():
            if ticker:
                out.add(str(ticker).upper())
        con.close()
    except Exception:
        pass
    return out


def _alpha_symbols() -> set[str]:
    out: set[str] = set()
    for artifact in (
        cfg.CACHE_DIR / "research" / "alpha_discovery_board_latest.json",
        cfg.CACHE_DIR / "research" / "alpha_discovery_overlay_latest.json",
    ):
        data = _load_json(artifact)
        for row in data.get("items") or []:
            sym = str(row.get("ticker") or "").upper()
            if sym:
                out.add(sym)
    return out


def _alpha_board_context() -> Dict[str, Dict[str, Any]]:
    """Returns {TICKER: {alpha_tier, alpha_score, actionable_now, alpha_track}}
    by reading the cached Alpha Discovery board + overlay. Cache-only — no
    provider calls. Empty dict on any failure (defensive)."""
    out: Dict[str, Dict[str, Any]] = {}
    for artifact in (
        cfg.CACHE_DIR / "research" / "alpha_discovery_board_latest.json",
        cfg.CACHE_DIR / "research" / "alpha_discovery_overlay_latest.json",
    ):
        data = _load_json(artifact)
        for row in data.get("items") or []:
            sym = str(row.get("ticker") or "").upper()
            if not sym:
                continue
            # Keep the row with the higher alpha_score if a ticker appears
            # on both the board and the overlay.
            existing = out.get(sym)
            new_score = _f(row.get("alpha_score"))
            if existing and _f(existing.get("alpha_score")) >= new_score:
                continue
            out[sym] = {
                "on_alpha_board": True,
                "alpha_tier": str(row.get("data_tier") or "").upper(),
                "alpha_score": new_score,
                "actionable_now": bool(row.get("actionable_now")),
                "alpha_track": str(row.get("track") or ""),
                "alpha_bucket": str(row.get("bucket") or ""),
            }
    return out


def _stock_lens_for(sym: str) -> Dict[str, Any]:
    """Read cache/research/stock_lens_<SYM>_latest.json and return a compact
    summary: {lens_composite, lens_label, lens_confidence}. Returns {} if
    the lens file is missing, zero-byte, or unreadable. Cache-only."""
    path = cfg.CACHE_DIR / "research" / f"stock_lens_{sym}_latest.json"
    if not path.exists() or path.stat().st_size == 0:
        return {}
    data = _load_json(path)
    if not data:
        return {}
    scores = data.get("scores") or {}
    composite = scores.get("composite")
    return {
        "lens_composite": float(composite) if isinstance(composite, (int, float)) else None,
        "lens_label": str(data.get("label") or ""),
        "lens_confidence": str(data.get("confidence") or ""),
    }


def _internal_corroboration_context(symbols: Sequence[str], enabled: bool) -> Dict[str, Dict[str, Any]]:
    """Merge alpha-board context + per-ticker stock-lens summary.  Cache-only,
    no provider calls.  Returns {} for symbols with no internal signal so the
    score block contributes zero (does not penalize, only rewards)."""
    if not enabled:
        return {}
    alpha_ctx = _alpha_board_context()
    out: Dict[str, Dict[str, Any]] = {}
    for sym in symbols:
        sym = sym.upper()
        merged: Dict[str, Any] = dict(alpha_ctx.get(sym) or {})
        merged.update(_stock_lens_for(sym))
        if merged:
            out[sym] = merged
    return out


def _novelty_context(symbols: Sequence[str], enabled: bool, lookback_days: int = 14) -> Dict[str, Dict[str, Any]]:
    """Returns {sym: {has_recent_activity, recent_signal_count, recent_decision_count}}.
    Looks back N days in paper_signals + decisions DB tables.  Cache-only — no
    provider calls.  Empty dict on any error (defensive — defaults to 'novelty')."""
    if not enabled or not symbols:
        return {}
    out: Dict[str, Dict[str, Any]] = {sym.upper(): {
        "has_recent_activity": False,
        "recent_signal_count": 0,
        "recent_decision_count": 0,
    } for sym in symbols}
    try:
        con = sqlite3.connect(str(cfg.DB_PATH), timeout=3)
        try:
            placeholders = ",".join("?" * len(symbols))
            uppers = [s.upper() for s in symbols]
            for table, ts_col, target in (
                ("paper_signals", "ts",  "recent_signal_count"),
                ("decisions",     "ts",  "recent_decision_count"),
            ):
                try:
                    rows = con.execute(
                        f"SELECT ticker, COUNT(*) FROM {table} "
                        f"WHERE ticker IN ({placeholders}) "
                        f"AND {ts_col} > datetime('now', ?) "
                        f"GROUP BY ticker",
                        (*uppers, f"-{int(lookback_days)} days"),
                    ).fetchall()
                    for ticker, count in rows:
                        sym_u = str(ticker).upper()
                        if sym_u in out:
                            out[sym_u][target] = int(count)
                            if int(count) > 0:
                                out[sym_u]["has_recent_activity"] = True
                except sqlite3.Error:
                    # Table or column may not exist on older schemas — skip.
                    continue
        finally:
            con.close()
    except Exception:
        pass
    return out


def _macro_window_context(enabled: bool, minutes_window: int = 60) -> List[Dict[str, Any]]:
    """Returns a list of {start, end, event, impact} for HIGH-impact macro
    events within the next ~7 days.  Reads FMP economic_calendar via the
    cached client.  An item whose news_timestamp falls inside any window
    is flagged 'in_macro_window' for downstream down-weighting / veto."""
    if not enabled:
        return []
    try:
        fmp = get_fmp()
        events = fmp.get_economic_calendar(days_ahead=7) or []
    except Exception:
        return []
    out: List[Dict[str, Any]] = []
    for ev in events:
        impact = str(ev.get("impact") or "").lower()
        if impact != "high":
            continue
        raw_dt = ev.get("date") or ev.get("datetime") or ev.get("timestamp")
        if not raw_dt:
            continue
        try:
            # FMP returns "YYYY-MM-DD HH:MM:SS" in UTC for economic events
            edt = datetime.strptime(str(raw_dt)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except Exception:
            try:
                edt = datetime.fromisoformat(str(raw_dt).replace("Z", "+00:00"))
                if edt.tzinfo is None:
                    edt = edt.replace(tzinfo=timezone.utc)
            except Exception:
                continue
        out.append({
            "start": edt - timedelta(minutes=minutes_window),
            "end":   edt + timedelta(minutes=minutes_window),
            "event": str(ev.get("event") or ev.get("name") or ""),
            "impact": "high",
        })
    return out


def _fmp_sentiment_context(symbols: Sequence[str], enabled: bool, stats: Dict[str, Any]) -> Dict[str, float]:
    """{sym: sentiment 0.0–1.0 (0.5=neutral)} via FMP get_sentiment_score.
    Skipped when --skip-fmp-sentiment.  Each lookup is FMP-cached (24h)."""
    if not enabled or not symbols:
        return {}
    out: Dict[str, float] = {}
    try:
        fmp = get_fmp()
    except Exception:
        return {}
    for sym in symbols:
        try:
            score = fmp.get_sentiment_score(sym)
            stats["api_attempts"]["fmp_sentiment"] = stats["api_attempts"].get("fmp_sentiment", 0) + 1
            if score is not None:
                out[sym.upper()] = float(score)
        except Exception:
            continue
    return out


def _insider_cluster_context(symbols: Sequence[str], enabled: bool, stats: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """{sym: {available, cluster_signal, recent_buys, recent_sells, net_value, window_days}}.
    Cluster-buy detector: any ticker with ≥2 distinct insider BUYS in the last
    14 days flags 'cluster_buy'; ≥2 SELLS flags 'cluster_sell'.  Higher net
    transaction value strengthens the signal."""
    if not enabled or not symbols:
        return {}
    try:
        fmp = get_fmp()
    except Exception:
        return {}
    cutoff = datetime.now(timezone.utc) - timedelta(days=14)
    out: Dict[str, Dict[str, Any]] = {}
    for sym in symbols:
        sym_u = sym.upper()
        try:
            rows = fmp.get_insider_trading(sym_u, limit=40) or []
            stats["api_attempts"]["fmp_insider"] = stats["api_attempts"].get("fmp_insider", 0) + 1
        except Exception:
            rows = []
        if not rows:
            out[sym_u] = {"available": False, "detail": "no insider data"}
            continue
        recent_buys: List[Dict[str, Any]] = []
        recent_sells: List[Dict[str, Any]] = []
        for r in rows:
            ts_raw = r.get("transactionDate") or r.get("filingDate") or r.get("date")
            if not ts_raw:
                continue
            try:
                ts = datetime.strptime(str(ts_raw)[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except Exception:
                continue
            if ts < cutoff:
                continue
            tx_type = str(r.get("transactionType") or r.get("acquisitionOrDisposition") or "").upper()
            value = _f(r.get("securitiesTransacted") or 0) * _f(r.get("price") or 0)
            if "P-PURCHASE" in tx_type or tx_type == "A" or "BUY" in tx_type:
                recent_buys.append({"name": r.get("reportingName") or "", "value": value, "date": str(ts_raw)[:10]})
            elif "S-SALE" in tx_type or tx_type == "D" or "SELL" in tx_type:
                recent_sells.append({"name": r.get("reportingName") or "", "value": value, "date": str(ts_raw)[:10]})
        n_buy = len({x["name"] for x in recent_buys})
        n_sell = len({x["name"] for x in recent_sells})
        net_value = sum(x["value"] for x in recent_buys) - sum(x["value"] for x in recent_sells)
        signal = "neutral"
        if n_buy >= 2 and n_buy > n_sell:
            signal = "cluster_buy"
        elif n_sell >= 2 and n_sell > n_buy:
            signal = "cluster_sell"
        out[sym_u] = {
            "available": True,
            "cluster_signal": signal,
            "distinct_buyers_14d": n_buy,
            "distinct_sellers_14d": n_sell,
            "net_value_14d": round(net_value, 0),
            "window_days": 14,
            "detail": f"{signal} · buyers={n_buy} sellers={n_sell} net=${net_value/1e6:+.1f}M",
        }
    return out


def _analyst_changes_context(symbols: Sequence[str], enabled: bool, stats: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """{sym: {available, recent_upgrades, recent_downgrades, latest_action, latest_firm, latest_date}}.
    Counts actions in the last 21 days from FMP /grades."""
    if not enabled or not symbols:
        return {}
    try:
        fmp = get_fmp()
    except Exception:
        return {}
    cutoff = datetime.now(timezone.utc) - timedelta(days=21)
    out: Dict[str, Dict[str, Any]] = {}
    for sym in symbols:
        sym_u = sym.upper()
        try:
            rows = fmp.get_analyst_grades(sym_u, limit=40) or []
            stats["api_attempts"]["fmp_grades"] = stats["api_attempts"].get("fmp_grades", 0) + 1
        except Exception:
            rows = []
        if not rows:
            out[sym_u] = {"available": False, "detail": "no analyst data"}
            continue
        ups = downs = 0
        latest: Optional[Dict[str, Any]] = None
        for r in rows:
            ts_raw = r.get("publishedDate") or r.get("date") or r.get("gradingDate")
            if not ts_raw:
                continue
            try:
                ts = datetime.strptime(str(ts_raw)[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except Exception:
                continue
            if ts < cutoff:
                continue
            action = str(r.get("action") or r.get("newGrade") or "").lower()
            prev_grade = str(r.get("previousGrade") or "").lower()
            new_grade = str(r.get("newGrade") or "").lower()
            is_up   = "up" in action or any(w in new_grade for w in ("buy", "outperform", "overweight")) and "sell" not in prev_grade
            is_down = "down" in action or any(w in new_grade for w in ("sell", "underperform", "underweight"))
            if is_up:   ups += 1
            if is_down: downs += 1
            if latest is None:
                latest = {"firm": r.get("gradingCompany") or r.get("analystCompany") or "",
                          "action": action or new_grade,
                          "date": str(ts_raw)[:10]}
        out[sym_u] = {
            "available": True,
            "recent_upgrades_21d": ups,
            "recent_downgrades_21d": downs,
            "latest_firm": (latest or {}).get("firm", ""),
            "latest_action": (latest or {}).get("action", ""),
            "latest_date": (latest or {}).get("date", ""),
            "detail": f"up={ups} down={downs}" + (f" · {latest['firm']}: {latest['action']} {latest['date']}" if latest else ""),
        }
    return out


def _alpaca_tape_context(symbols: Sequence[str], enabled: bool, stats: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """5-min Alpaca minute bars (≤8h lookback) for tape confirmation.  Returns
    schema-compatible with _fetch_yfinance_tape so it can be a drop-in
    replacement (volume_ratio_5d derived from minute bars vs 20d daily ADV)."""
    if not enabled or not symbols:
        return {}
    try:
        from core.alpaca_client import AlpacaClient
        ac = AlpacaClient()
    except Exception as exc:
        stats["source_errors"].append(f"Alpaca tape unavailable: {type(exc).__name__}: {exc}")
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for sym in symbols:
        sym_u = sym.upper()
        try:
            bars = ac.get_intraday_bars(sym_u, timeframe="5Min", limit=78)
            stats["api_attempts"]["alpaca_bars"] = stats["api_attempts"].get("alpaca_bars", 0) + 1
        except Exception:
            bars = []
        if not bars:
            continue
        try:
            price = _f(bars[-1].get("close"))
            cumulative_vol = sum(_f(b.get("volume")) for b in bars)
            cumulative_dvol = sum(_f(b.get("volume")) * _f(b.get("close")) for b in bars)
            opens = [_f(b.get("open")) for b in bars if _f(b.get("open"))]
            first_open = opens[0] if opens else price
            intraday_ret_pct = ((price - first_open) / first_open * 100.0) if first_open else 0.0
            out[sym_u] = {
                "source": "alpaca",
                "available": True,
                "price": round(price, 2),
                "avg_dollar_volume_20": round(cumulative_dvol, 2),   # session-cumulative — used as floor only
                "return_5d_pct": round(intraday_ret_pct, 2),          # session intraday for tape_score
                "return_20d_pct": round(intraday_ret_pct, 2),
                "volume_ratio_5d": 1.0,  # Alpaca path can't compute 5d/20d ratio without daily bars — neutral
                "bars_stale": False,
                "confirmation": (price >= 5.0) and (cumulative_dvol >= 5_000_000) and (abs(intraday_ret_pct) >= 1.0),
            }
        except Exception:
            continue
    return out


def _collect_alpaca_news(limit: int, stats: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Pull recent market news from Alpaca's News API (Benzinga-derived).
    Schema-normalized to match the FMP/NewsAPI shape used by collect_raw_items."""
    out: List[Dict[str, Any]] = []
    try:
        from alpaca.data.historical.news import NewsClient
        from alpaca.data.requests import NewsRequest
    except Exception as exc:
        stats["source_errors"].append(f"Alpaca news SDK unavailable: {type(exc).__name__}: {exc}")
        return out
    try:
        key = os.getenv("ALPACA_API_KEY", "").strip()
        secret = os.getenv("ALPACA_SECRET_KEY", "").strip()
        if not key or not secret or key == "offline":
            stats["source_errors"].append("Alpaca news: credentials not set")
            return out
        client = NewsClient(api_key=key, secret_key=secret)
        req = NewsRequest(start=datetime.now(timezone.utc) - timedelta(hours=48), limit=int(limit), include_content=False)
        resp = client.get_news(req)
        # alpaca-py NewsSet exposes `.data` as {"news": [News, ...]}.  It does
        # NOT have a `.news` attribute on the response itself.
        items = (resp.data.get("news") if hasattr(resp, "data") and isinstance(resp.data, dict) else []) or []
    except Exception as exc:
        stats["source_errors"].append(f"Alpaca news fetch failed: {type(exc).__name__}: {exc}")
        return out
    for item in items or []:
        try:
            # `item` is a Pydantic News model; attributes (not dict keys).
            symbols = list(getattr(item, "symbols", None) or [])
            title = getattr(item, "headline", "") or ""
            url = getattr(item, "url", "") or ""
            summary = getattr(item, "summary", "") or ""
            ts = getattr(item, "created_at", None)
            if isinstance(ts, datetime):
                ts = ts.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            for sym in (symbols or [None]):
                out.append({
                    "source_type": "alpaca_news",
                    "source": "Alpaca (Benzinga)",
                    "symbol": str(sym or "").upper(),
                    "title": title,
                    "description": summary,
                    "url": url,
                    "timestamp": str(ts or "")[:19],
                })
        except Exception:
            continue
    stats["source_status"]["alpaca_news"] = f"{len(out)} raw items"
    return out


# Aliases whose generic-English usage swamps the company signal badly enough
# that the bare word is uninformative — in BOTH the Google-Trends term picker
# (a spike on the bare word means nothing) AND the news-mapping path (the word
# appears in unrelated articles, e.g. "premium content strategy" → MSTR). We
# skip these unless corroborated by a stronger signal for the same ticker.
# This is conservative on purpose — most canonical brand names
# (apple/google/tesla/amazon) dominate their term even after accounting for
# dictionary usage, and excluding them would lose real signal. If a term proves
# useful, replace it with a less ambiguous phrase ("target stock", "snap inc")
# rather than deleting the entry.
AMBIGUOUS_ALIAS_TERMS = frozenset({
    "target",   # TGT — verb / common noun, swamps the retail store
    "snap",     # SNAP — common verb (snap photo, snap decision)
    "block",    # not in current aliases, listed defensively
    "strategy", # MSTR's secondary alias — extremely generic word
    "meta",     # META — "meta" / "metadata" / "meta-analysis" in prose
})

# Back-compat alias: this set was historically named for the Trends path only.
# It now guards news mapping too; the old name is preserved for callers/tests.
AMBIGUOUS_TRENDS_TERMS = AMBIGUOUS_ALIAS_TERMS

# Absolute floor on pytrends 0-100 *relative* index. A spike from 1→5 has
# infinite z but carries no real signal — the search term simply isn't queried
# enough for the spike to mean anything. Filters bare-ticker noise and low-
# volume aliases.
TRENDS_MIN_LATEST = 20.0


def _google_trends_term(sym: str) -> str:
    """Pick the cleanest search term for a ticker.

    Prefers the first non-ambiguous COMPANY_ALIASES entry over the raw ticker —
    bare tickers (e.g. "F", "GO", "T") are ambiguous English words and pollute
    the signal. Aliases listed in AMBIGUOUS_TRENDS_TERMS are also skipped.
    Falls back to ticker only when no usable alias exists.
    """
    aliases = COMPANY_ALIASES.get(sym) or []
    for alias in aliases:
        if alias.lower() not in AMBIGUOUS_ALIAS_TERMS:
            return alias
    return sym


def _fetch_google_trends(
    symbols: Sequence[str],
    stats: Dict[str, Any],
    z_threshold: float = 2.0,
    timeframe: str = "now 7-d",
    overall_budget_seconds: float = 45.0,
) -> List[Dict[str, Any]]:
    """Surface tickers with an interest-spike in Google Trends.

    Cache-friendly research source: pytrends queries are unofficial,
    rate-limited, and best-effort. This function NEVER raises — every error
    path logs a single warning to `stats["source_errors"]` and returns whatever
    spikes were collected so the radar can proceed without Trends. Output
    rows match the news shape consumed by collect_raw_items → normalize_items.

    Graceful-degradation contract:
      - pytrends missing → return [] with one error logged.
      - urllib3 shim failure → return [] with one error logged.
      - 429 / rate-limit → stop iterating chunks, return what we have, one warning.
      - schema change (DataFrame shape/columns) → skip the affected chunk.
      - per-call timeout → caught by pytrends's own retry layer; outer
        overall_budget_seconds caps total wallclock to avoid hanging the radar.
    """
    import time as _time

    deadline = _time.monotonic() + max(5.0, float(overall_budget_seconds))
    out: List[Dict[str, Any]] = []
    if not symbols:
        stats["source_status"]["google_trends"] = "no symbols supplied"
        return out
    try:
        # pytrends 4.9.2 calls urllib3 Retry with the deprecated `method_whitelist`
        # kwarg; urllib3 v2 removed it. Shim it to `allowed_methods` before the
        # pytrends import so its Retry construction works against either urllib3.
        from urllib3.util.retry import Retry as _Retry  # type: ignore
        _orig_retry_init = _Retry.__init__

        def _patched_retry_init(self, *a, **kw):  # type: ignore
            if "method_whitelist" in kw and "allowed_methods" not in kw:
                kw["allowed_methods"] = kw.pop("method_whitelist")
            return _orig_retry_init(self, *a, **kw)

        _Retry.__init__ = _patched_retry_init  # type: ignore
        from pytrends.request import TrendReq  # type: ignore
    except Exception as exc:
        stats["source_errors"].append(
            f"google_trends skipped: pytrends not installed ({type(exc).__name__}: {exc})"
        )
        stats["source_status"]["google_trends"] = "pytrends missing"
        return out

    # pytrends caps each build_payload at 5 terms. Keep a per-symbol mapping so
    # we can recover the originating ticker from the search term.
    term_to_sym: Dict[str, str] = {}
    for sym in symbols:
        term = _google_trends_term(sym)
        # last-write-wins is fine: GOOGL/GOOG share "google" and collapsing to
        # one row matches how news cross-mentions are normalized downstream.
        term_to_sym[term] = sym
    terms = list(term_to_sym.keys())

    try:
        py = TrendReq(hl="en-US", tz=300, timeout=(5, 15), retries=1, backoff_factor=0.3)
    except Exception as exc:
        stats["source_errors"].append(f"google_trends client init failed: {type(exc).__name__}: {exc}")
        stats["source_status"]["google_trends"] = "client init failed"
        return out

    spikes: List[Tuple[str, float, float]] = []  # (sym, latest, z)
    chunks = [terms[i : i + 5] for i in range(0, len(terms), 5)]
    rate_limit_hit = False
    for chunk in chunks:
        if _time.monotonic() > deadline:
            stats["source_errors"].append(
                f"google_trends: overall budget {overall_budget_seconds:.0f}s "
                f"exhausted; stopping after {stats['api_attempts'].get('google_trends', 0)} chunk(s)"
            )
            break
        try:
            py.build_payload(chunk, timeframe=timeframe, geo="US")
            df = py.interest_over_time()
            stats["api_attempts"]["google_trends"] = stats["api_attempts"].get("google_trends", 0) + 1
        except Exception as exc:
            msg = f"{type(exc).__name__}: {exc}"
            # pytrends raises ResponseError or generic Exception on 429.
            if "429" in msg or "TooManyRequests" in type(exc).__name__:
                if not rate_limit_hit:
                    stats["source_errors"].append(
                        "google_trends: rate-limited (429); stopping further chunks for this run"
                    )
                    rate_limit_hit = True
                break  # don't keep hammering the API once rate-limited
            stats["source_errors"].append(
                f"google_trends fetch failed for {chunk}: {msg}"
            )
            continue
        # Schema-change resilience: if pytrends ever returns something that
        # isn't a DataFrame (or lacks .columns / .iloc), treat the chunk as a
        # parse miss rather than crashing.
        try:
            if df is None or getattr(df, "empty", True):
                continue
            # `df.columns` is a pandas Index — must convert explicitly. Using
            # `or []` here triggers "truth value of an Index is ambiguous".
            cols_attr = getattr(df, "columns", None)
            columns = list(cols_attr) if cols_attr is not None else []
        except Exception as exc:
            stats["source_errors"].append(
                f"google_trends: unexpected response shape for {chunk}: "
                f"{type(exc).__name__}: {exc}"
            )
            continue
        for term in chunk:
            if term not in columns:
                continue
            try:
                series = df[term].dropna()
            except Exception:
                continue
            # Need a baseline window of at least 6 observations and a non-zero
            # spread; otherwise z is meaningless.
            if len(series) < 7:
                continue
            try:
                latest = float(series.iloc[-1])
                baseline = series.iloc[:-1]
                mean = float(baseline.mean())
                std = float(baseline.std())
            except Exception:
                continue
            if std <= 0:
                continue
            z = (latest - mean) / std
            # Two-gate emission: relative spike (z) AND absolute relevance
            # (latest index). A high z on a term with low absolute index is
            # noise from rare-search ambiguous aliases.
            if (
                z >= z_threshold
                and latest >= TRENDS_MIN_LATEST
            ):
                spikes.append((term_to_sym[term], latest, z))

    now_iso = _utc_now().isoformat()
    for sym, latest, z in spikes:
        # Wording note: pytrends returns a 0-100 *relative* index normalized to
        # the highest point in the requested window — it is NOT absolute search
        # volume. The title spells this out so downstream readers don't
        # misinterpret "95/100" as raw query count.
        title = (
            f"Relative Google Trends spike for ${sym}: normalized interest "
            f"index {latest:.0f}/100 (z={z:+.2f} vs 7d baseline; index is "
            f"relative to window peak, not absolute search volume)"
        )
        out.append(
            {
                "source_type": "google_trends",
                "source": "Google Trends",
                "title": title,
                "description": (
                    f"Unofficial pytrends signal — term={_google_trends_term(sym)!r}. "
                    f"Index is relative (0-100 normalized to 7d window peak), not absolute. "
                    f"Corroboration only — must NOT be treated as an independent news source."
                ),
                "timestamp": now_iso,
                "url": f"https://trends.google.com/trends/explore?q={_google_trends_term(sym)}&geo=US",
                "symbol": sym,
                "raw": {
                    "latest_relative_index": latest,
                    "z_vs_7d_baseline": z,
                    "timeframe": timeframe,
                    "is_corroboration_only": True,
                },
            }
        )
    stats["source_status"]["google_trends"] = (
        f"{len(out)} spike(s) from {len(terms)} term(s)"
    )
    return out


def _news_in_macro_window(news_timestamp: str, macro_windows: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Return the first matching macro window if news_timestamp falls inside,
    else None.  Used to down-weight social leads landing on a HIGH-impact
    macro release."""
    if not macro_windows or not news_timestamp:
        return None
    try:
        ts = datetime.fromisoformat(news_timestamp.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
    except Exception:
        return None
    for w in macro_windows:
        if w["start"] <= ts <= w["end"]:
            return w
    return None


def _bte_symbols(snapshot: Dict[str, Any]) -> set[str]:
    try:
        from core.research_assist_bte import build_research_bte

        bte = build_research_bte(universe_snapshot=snapshot, regime={}, vix=None)
        return {
            str(row.get("symbol") or "").upper()
            for row in bte.focus_names
            if row.get("symbol")
        }
    except Exception:
        return set()


def _collect_fmp_news(limit: int, stats: Dict[str, Any]) -> List[Dict[str, Any]]:
    try:
        page_limit = max(20, min(100, limit * 5))
        rows = get_fmp().get_news(ticker=None, limit=page_limit)
        stats["api_attempts"]["fmp_news"] = stats["api_attempts"].get("fmp_news", 0) + 1
        return [
            {
                "source_type": "fmp_stock_news",
                "source": item.get("site") or "FMP",
                "title": item.get("title") or "",
                "description": item.get("text") or "",
                "timestamp": item.get("publishedDate") or item.get("date") or "",
                "url": item.get("url") or "",
                "symbol": item.get("symbol") or item.get("ticker") or "",
                "raw": item,
            }
            for item in rows
            if item.get("title")
        ]
    except Exception as exc:
        stats["source_errors"].append(f"FMP news unavailable: {type(exc).__name__}: {exc}")
        return []


def _collect_news_api(limit: int, stats: Dict[str, Any]) -> List[Dict[str, Any]]:
    api_key = os.getenv("NEWS_API_KEY", "").strip()
    if not api_key:
        stats["source_errors"].append("News API skipped: NEWS_API_KEY not set")
        return []

    queries = [
        '(earnings OR guidance OR acquisition OR FDA OR contract OR tariff) AND (stock OR shares)',
        '("data center" OR semiconductor OR nuclear OR uranium OR crypto OR cybersecurity OR defense)',
    ]
    rows: List[Dict[str, Any]] = []
    from_date = (_utc_now() - timedelta(days=7)).date().isoformat()
    page_size = max(10, min(50, limit * 2))
    for query in queries:
        try:
            resp = requests.get(
                "https://newsapi.org/v2/everything",
                params={
                    "q": query,
                    "from": from_date,
                    "sortBy": "publishedAt",
                    "language": "en",
                    "pageSize": page_size,
                    "apiKey": api_key,
                },
                timeout=15,
            )
            stats["api_attempts"]["news_api"] = stats["api_attempts"].get("news_api", 0) + 1
            resp.raise_for_status()
            payload = resp.json()
            if payload.get("status") == "error":
                stats["source_errors"].append(f"News API error: {payload.get('message', 'unknown')}")
                continue
            for item in payload.get("articles") or []:
                title = item.get("title") or ""
                if not title:
                    continue
                rows.append(
                    {
                        "source_type": "news_api",
                        "source": ((item.get("source") or {}).get("name") or "News API"),
                        "title": title,
                        "description": item.get("description") or "",
                        "timestamp": item.get("publishedAt") or "",
                        "url": item.get("url") or "",
                        "symbol": "",
                        "raw": item,
                    }
                )
        except Exception as exc:
            stats["source_errors"].append(f"News API unavailable: {type(exc).__name__}: {exc}")
    return rows


def _offline_sample_items() -> List[Dict[str, Any]]:
    now = _utc_now().isoformat()
    return [
        {
            "source_type": "fmp_stock_news",
            "source": "FMP",
            "title": "Nvidia data-center demand lifts AI server suppliers as analysts raise forecasts",
            "description": "AI data center demand and GPU supply chain update.",
            "timestamp": now,
            "url": "https://example.com/nvda-ai-demand",
            "symbol": "NVDA",
            "raw": {},
        },
        {
            "source_type": "news_api",
            "source": "Sample News",
            "title": "Power grid bottlenecks put nuclear suppliers back on investor radar",
            "description": "Nuclear and grid demand theme with power producers.",
            "timestamp": now,
            "url": "https://example.com/nuclear-grid-demand",
            "symbol": "",
            "raw": {},
        },
        {
            "source_type": "news_api",
            "source": "Sample News",
            "title": "Stocks mixed before the bell as traders watch the Fed",
            "description": "Generic market update.",
            "timestamp": now,
            "url": "https://example.com/generic-market-wrap",
            "symbol": "",
            "raw": {},
        },
        {
            "source_type": "news_api",
            "source": "Sample News",
            "title": "Tiny penny stock rockets as social media predicts a short squeeze",
            "description": "Pump-like low quality story.",
            "timestamp": now,
            "url": "https://example.com/penny-squeeze",
            "symbol": "ZZZZ",
            "raw": {},
        },
    ]


def collect_raw_items(args: argparse.Namespace, stats: Dict[str, Any]) -> List[Dict[str, Any]]:
    if args.offline_sample:
        stats["source_status"]["offline_sample"] = "used"
        return _offline_sample_items()

    raw: List[Dict[str, Any]] = []
    if not args.skip_fmp:
        fmp_rows = _collect_fmp_news(args.limit, stats)
        raw.extend(fmp_rows)
        stats["source_status"]["fmp_news"] = f"{len(fmp_rows)} raw items"
    else:
        stats["source_status"]["fmp_news"] = "skipped by flag"

    if not args.skip_newsapi:
        news_rows = _collect_news_api(args.limit, stats)
        raw.extend(news_rows)
        stats["source_status"]["news_api"] = f"{len(news_rows)} raw items"
    else:
        stats["source_status"]["news_api"] = "skipped by flag"

    if not getattr(args, "skip_alpaca_news", False):
        alpaca_rows = _collect_alpaca_news(args.limit, stats)
        raw.extend(alpaca_rows)
    else:
        stats["source_status"]["alpaca_news"] = "skipped by flag"

    if not getattr(args, "skip_google_trends", False):
        # Restrict to tickers with a curated alias — bare-ticker searches are
        # noisy ("F", "GO", "T"). Prefer FALLBACK_SYMBOLS overlap first so the
        # most market-relevant names are checked when the limit bites.
        trend_universe = [s for s in COMPANY_ALIASES.keys() if s in FALLBACK_SYMBOLS]
        for s in sorted(COMPANY_ALIASES.keys()):
            if s not in trend_universe:
                trend_universe.append(s)
        trend_limit = max(0, int(getattr(args, "google_trends_limit", 30)))
        trend_symbols = trend_universe[:trend_limit] if trend_limit else []
        gt_rows = _fetch_google_trends(
            trend_symbols,
            stats,
            z_threshold=float(getattr(args, "google_trends_z", 2.0)),
        )
        raw.extend(gt_rows)
    else:
        stats["source_status"]["google_trends"] = "skipped by flag"

    return raw


def normalize_items(raw_items: Sequence[Dict[str, Any]], known_symbols: set[str]) -> List[NormalizedItem]:
    normalized: List[NormalizedItem] = []
    seen_raw: set[str] = set()
    for idx, raw in enumerate(raw_items):
        title = " ".join(str(raw.get("title") or "").split())
        if not title:
            continue
        source_type = str(raw.get("source_type") or "unknown")
        source = str(raw.get("source") or source_type)
        url = str(raw.get("url") or "")
        dt = _parse_dt(raw.get("timestamp"))
        ts = (dt or _utc_now()).isoformat()
        theme = _theme_for_text(title, str(raw.get("description") or ""))
        # Per-ticker evidence, location-aware. We keep the headline and the body
        # separate, and track a ticker's *subject* status (the FMP `symbol` tag),
        # so the downstream confidence reflects WHERE and HOW the match was found:
        # a clean alias in the headline is worth far more than a generic word
        # buried in the body — the "premium content strategy" → MSTR class of
        # false positive that motivated this layer.
        title_text = f" {title} "
        body_text = f" {raw.get('description') or ''} "
        title_low = title_text.lower()
        body_low = body_text.lower()

        evidence: Dict[str, Dict[str, Any]] = {}

        def _ev(sym: str) -> Dict[str, Any]:
            return evidence.setdefault(
                sym,
                {
                    "in_title": False,
                    "in_body": False,
                    "is_subject": False,
                    "direct_symbol": False,
                    "ambiguous_hit": False,
                    "alias": None,
                },
            )

        # FMP tags each news row with its primary subject symbol. Trust it as a
        # subject signal — but it does NOT count as headline presence.
        raw_symbol = str(raw.get("symbol") or "").upper().strip()
        if raw_symbol and raw_symbol not in KNOWN_INSTRUMENTS and raw_symbol not in COMMON_FALSE_TICKERS:
            _ev(raw_symbol)["is_subject"] = True

        # Pass 1 — $-prefixed tickers: always trustworthy, allow 1-5 letters.
        for scope, in_title in ((title_text, True), (body_text, False)):
            for sym in re.findall(r"\$([A-Z]{1,5})\b", scope):
                sym = sym.upper()
                if sym in known_symbols and sym not in KNOWN_INSTRUMENTS and sym not in COMMON_FALSE_TICKERS:
                    e = _ev(sym)
                    e["direct_symbol"] = True
                    e["in_title" if in_title else "in_body"] = True
        # Pass 2 — bare uppercase tokens: require 2-5 letters AND reject any
        # token sitting adjacent to "&" (catches the S&P → P / AT&T → T /
        # P&G → G class of false positives). Single-letter tickers in news text
        # without a $-prefix are essentially always false positives, so they are
        # intentionally NOT matched here — they can still arrive via the raw
        # `symbol` field above or via COMPANY_ALIASES below.
        for scope, in_title in ((title_text, True), (body_text, False)):
            for m in re.finditer(r"\b([A-Z]{2,5})\b", scope):
                sym = m.group(1).upper()
                start, end = m.span(1)
                prev_c = scope[start - 1] if start > 0 else " "
                next_c = scope[end] if end < len(scope) else " "
                if prev_c == "&" or next_c == "&":
                    continue
                if sym in known_symbols and sym not in KNOWN_INSTRUMENTS and sym not in COMMON_FALSE_TICKERS:
                    e = _ev(sym)
                    e["direct_symbol"] = True
                    e["in_title" if in_title else "in_body"] = True

        # Company-name aliases — word-boundary matched, so "strategy" does not
        # match inside "strategical" and multi-word aliases match as phrases.
        # Generic English words (AMBIGUOUS_ALIAS_TERMS) are recorded only as a
        # weak `ambiguous_hit`; they do NOT establish title/body presence on
        # their own and are dropped at mapping time unless corroborated.
        for sym, aliases in COMPANY_ALIASES.items():
            if sym not in known_symbols:
                continue
            for alias in aliases:
                alias_low = alias.lower()
                pat = re.compile(rf"\b{re.escape(alias_low)}\b")
                hit_title = bool(pat.search(title_low))
                hit_body = bool(pat.search(body_low))
                if not (hit_title or hit_body):
                    continue
                e = _ev(sym)
                if alias_low in AMBIGUOUS_ALIAS_TERMS:
                    e["ambiguous_hit"] = True
                    continue
                if hit_title:
                    e["in_title"] = True
                if hit_body:
                    e["in_body"] = True
                if e["alias"] is None:
                    e["alias"] = alias

        tickers_mentioned = sorted(evidence.keys())
        company_names = sorted({e["alias"] for e in evidence.values() if e["alias"]})

        item_id = _stable_id(source_type, source, title, url)
        if item_id in seen_raw:
            continue
        seen_raw.add(item_id)
        normalized.append(
            NormalizedItem(
                item_id=item_id,
                title=title,
                source=source,
                source_type=source_type,
                timestamp=ts,
                freshness_hours=round(_age_hours(dt), 2),
                url=url,
                tickers_mentioned=tickers_mentioned,
                company_names=company_names,
                theme=theme,
                source_payload_ref=f"raw[{idx}]",
                ticker_evidence=evidence,
            )
        )
    return normalized


def _is_direct_ticker_mention(sym: str, title: str) -> bool:
    """A ticker counts as a 'direct mention' only if:
      - it appears $-prefixed (e.g. "$AAPL"), OR
      - it is 2+ letters AND surrounded by word boundaries AND not adjacent
        to "&" (the latter rejects S&P/AT&T/P&G-style false positives).
    Single-letter tickers without "$" prefix are NEVER counted as direct
    mentions because "S&P" / "AT&T" / "P&G" / standalone capitalized words
    produce too many false positives in news text.
    """
    if f"${sym}" in title:
        return True
    if len(sym) < 2:
        return False
    for m in re.finditer(rf"\b{re.escape(sym)}\b", title):
        start, end = m.span()
        prev_c = title[start - 1] if start > 0 else " "
        next_c = title[end] if end < len(title) else " "
        if prev_c == "&" or next_c == "&":
            continue
        return True
    return False


def _confidence_from_evidence(ev: Dict[str, Any]) -> Tuple[float, str, str, bool, bool]:
    """Location-aware mapping confidence → (conf, method, label, in_title, is_subject).

    Strongest first:
      • ticker token / $-mention in the headline → 0.95 direct_ticker
      • clean company-name alias in the headline → 0.82 company_name
      • FMP-tagged article subject               → 0.80 fmp_subject
      • ticker token only in the body            → 0.55 direct_ticker_body
      • clean company-name alias only in the body → 0.45 company_name_body
      • only a generic-word alias hit            → 0.00 ambiguous_alias (suppressed,
        dropped below the 0.40 floor in build_story_groups)
    """
    in_title = bool(ev.get("in_title"))
    is_subject = bool(ev.get("is_subject"))
    direct_symbol = bool(ev.get("direct_symbol"))
    has_clean_evidence = in_title or bool(ev.get("in_body")) or is_subject or direct_symbol
    if not has_clean_evidence:
        # Only a generic English word (e.g. "strategy") matched — uninformative.
        return 0.0, "ambiguous_alias", "ambiguous generic-word alias (suppressed)", False, False
    if direct_symbol and in_title:
        return 0.95, "direct_ticker", "direct ticker mention", True, is_subject
    if in_title:
        return 0.82, "company_name", "company name match (headline)", True, is_subject
    if is_subject:
        return 0.80, "fmp_subject", "FMP article subject tag", False, True
    if direct_symbol:
        return 0.55, "direct_ticker_body", "direct ticker mention (body)", False, False
    return 0.45, "company_name_body", "company name match (body)", False, False


def map_item_to_tickers(item: NormalizedItem, known_symbols: set[str]) -> List[TickerMapping]:
    mappings: List[TickerMapping] = []
    for sym in item.tickers_mentioned:
        ev = (item.ticker_evidence or {}).get(sym)
        if ev:
            conf, method, label, in_title, is_subject = _confidence_from_evidence(ev)
        else:
            # Back-compat fallback for items built without evidence (synthetic
            # Trends rows, hand-built test fixtures): title presence ⇒ direct.
            in_title = _is_direct_ticker_mention(sym, item.title)
            is_subject = False
            method = "direct_ticker" if in_title else "company_name"
            label = "direct ticker mention" if in_title else "company name match"
            conf = 0.95 if in_title else 0.82
        mappings.append(TickerMapping(sym, conf, method, label, in_title=in_title, is_subject=is_subject))

    if not mappings and item.theme in THEME_RULES:
        for sym in THEME_RULES[item.theme].get("tickers") or []:
            if sym in known_symbols and sym not in KNOWN_INSTRUMENTS:
                mappings.append(TickerMapping(sym, 0.42, "theme_inference", f"{item.theme} theme inference"))
        mappings = mappings[:4]

    by_ticker: Dict[str, TickerMapping] = {}
    for m in mappings:
        prev = by_ticker.get(m.ticker)
        if prev is None or m.confidence > prev.confidence:
            by_ticker[m.ticker] = m
    return list(by_ticker.values())


def _drop(drop_stats: Dict[str, Any], reason: str, title: str, ticker: str = "") -> None:
    drop_stats["total"] += 1
    drop_stats["reasons"][reason] = drop_stats["reasons"].get(reason, 0) + 1
    if len(drop_stats["examples"]) < 12:
        drop_stats["examples"].append({"ticker": ticker, "reason": reason, "title": _clip(title, 110)})


def build_story_groups(
    normalized: Sequence[NormalizedItem],
    known_symbols: set[str],
    drop_stats: Dict[str, Any],
) -> Tuple[Dict[str, Dict[str, Any]], List[NormalizedItem]]:
    """Group normalized news items into story-clusters keyed on ticker+story.

    Returns (groups, trend_items). Trend rows (source_type=='google_trends')
    bypass the market-relevance and ticker-mapping gates because a Trends
    spike is a *corroborating* signal, not a standalone thesis; they get
    attached to existing groups by ticker in _attach_trends_to_groups.
    """
    groups: Dict[str, Dict[str, Any]] = {}
    trend_items: List[NormalizedItem] = []
    for item in normalized:
        if item.source_type == "google_trends":
            trend_items.append(item)
            continue
        if item.freshness_hours > 168:
            _drop(drop_stats, "stale_source", item.title)
            continue
        if _is_generic_noise(item.title):
            _drop(drop_stats, "generic_or_non_actionable", item.title)
            continue
        if not _is_market_relevant(item.title, item.theme):
            _drop(drop_stats, "no_market_relevant_catalyst", item.title)
            continue
        mappings = map_item_to_tickers(item, known_symbols)
        if not mappings:
            _drop(drop_stats, "no_public_company_mapping", item.title)
            continue

        low = item.title.lower()
        for mapping in mappings:
            if mapping.method == "ambiguous_alias":
                # Generic English word matched a company alias (e.g. "strategy"
                # → MSTR) with no corroborating ticker/subject evidence.
                _drop(drop_stats, "ambiguous_generic_word_alias", item.title, mapping.ticker)
                continue
            # Corporate suffix guard: AG/SA/SE/NV/PLC are legal entity suffixes
            # that appear in company names ("Swiss Technology Group AG").  A bare
            # uppercase token match is not sufficient evidence that the news is
            # about the *stock*.  Require at least one of: explicit $-prefix,
            # a validated company-name alias, or an FMP subject tag.
            if mapping.ticker in CORPORATE_SUFFIX_TICKERS:
                ev = (item.ticker_evidence or {}).get(mapping.ticker) or {}
                has_dollar = bool(re.search(rf'\${re.escape(mapping.ticker)}\b', item.title))
                has_alias = bool(ev.get("alias"))       # COMPANY_ALIASES hit
                has_subject = bool(ev.get("is_subject"))  # FMP-tagged subject
                if not (has_dollar or has_alias or has_subject):
                    _drop(drop_stats, "ambiguous_generic_word_alias", item.title, mapping.ticker)
                    continue
            if mapping.confidence < 0.40:
                _drop(drop_stats, "weak_ticker_mapping", item.title, mapping.ticker)
                continue
            if mapping.ticker in KNOWN_INSTRUMENTS:
                _drop(drop_stats, "instrument_not_company", item.title, mapping.ticker)
                continue
            key = f"{mapping.ticker}:{_story_key(item.title, item.theme)}"
            group = groups.setdefault(
                key,
                {
                    "ticker": mapping.ticker,
                    "theme": item.theme,
                    "titles": [],
                    "urls": [],
                    "sources": set(),
                    "source_types": set(),
                    "freshness_hours": item.freshness_hours,
                    "mapping_confidence": mapping.confidence,
                    "mapping_method": mapping.method,
                    "mapping_labels": set(),
                    "noise_terms": [],
                    "in_title": False,
                    "is_subject": False,
                },
            )
            group["titles"].append(item.title)
            if item.url:
                group["urls"].append(item.url)
            group["sources"].add(item.source)
            group["source_types"].add(item.source_type)
            group["freshness_hours"] = min(float(group["freshness_hours"]), item.freshness_hours)
            if mapping.confidence > float(group["mapping_confidence"]):
                group["mapping_confidence"] = mapping.confidence
                group["mapping_method"] = mapping.method
            # Headline-presence flags drive the #4 sanity gate in score_candidates:
            # a ticker that is neither named in any headline nor the FMP subject
            # is a body-only mention and must not be surfaced as a real catalyst.
            if mapping.in_title:
                group["in_title"] = True
            if mapping.is_subject:
                group["is_subject"] = True
            group["mapping_labels"].add(mapping.label)
            if any(term in low for term in MEME_NOISE_TERMS):
                group["noise_terms"].append("meme/pump language")
    return groups, trend_items


_TREND_Z_RE = re.compile(r"z=([-+]?\d+(?:\.\d+)?)")
# Matches "index 95/100" or older "interest at 95/100" for back-compat with
# any stale raw artifacts still on disk.
_TREND_LATEST_RE = re.compile(r"(?:index|interest at) (\d+)/100")


def _parse_trend_signal(item: NormalizedItem) -> Tuple[Optional[float], Optional[float]]:
    """Pull (latest, z) from a synthetic Trends title. Returns (None, None) on parse miss."""
    z_match = _TREND_Z_RE.search(item.title)
    latest_match = _TREND_LATEST_RE.search(item.title)
    z = float(z_match.group(1)) if z_match else None
    latest = float(latest_match.group(1)) if latest_match else None
    return latest, z


def attach_trends_to_groups(
    groups: Dict[str, Dict[str, Any]],
    trend_items: Sequence[NormalizedItem],
    stats: Dict[str, Any],
    max_age_hours: float = 72.0,
) -> int:
    """Attach Trends spikes to existing news groups as a side-channel signal.

    Trends is recorded as a *corroborating* annotation on the group — it
    deliberately does NOT bump `sources`, `source_types`, or `urls`, because
    Google Trends is not an independent news source on equal footing with
    Reuters/FMP/etc. Bucket promotion uses the separate `has_google_trends`
    + `trend_z` fields; see `_bucket_for_candidate`.

    Returns the number of successful attachments for diagnostics.
    """
    if not trend_items or not groups:
        stats["source_status"].setdefault(
            "google_trends_attach",
            f"0 attached ({len(trend_items)} trend rows, {len(groups)} groups)",
        )
        return 0

    groups_by_ticker: Dict[str, List[Dict[str, Any]]] = {}
    for group in groups.values():
        groups_by_ticker.setdefault(str(group.get("ticker") or "").upper(), []).append(group)

    attached = 0
    for item in trend_items:
        ticker = next((t for t in item.tickers_mentioned if t), "")
        if not ticker:
            continue
        targets = groups_by_ticker.get(ticker.upper()) or []
        if not targets:
            continue
        latest, z = _parse_trend_signal(item)
        for group in targets:
            if float(group.get("freshness_hours") or 9999.0) > max_age_hours:
                continue
            # Side-channel only. Do NOT mutate sources/source_types/urls —
            # source_count must reflect independent news sources only.
            existing_z = group.get("trend_z")
            if z is not None and (existing_z is None or z > float(existing_z)):
                group["trend_z"] = z
                group["trend_latest"] = latest
                group["has_google_trends"] = True
                group["trend_source_label"] = item.source
                if item.url:
                    group["trend_url"] = item.url
            elif group.get("has_google_trends") is None:
                # No z parsed (parse miss), but still mark presence so the
                # candidate can be flagged in the artifact.
                group["has_google_trends"] = True
            attached += 1

    stats["source_status"]["google_trends_attach"] = (
        f"{attached} group attachment(s) from {len(trend_items)} trend row(s)"
    )
    return attached


def _metadata_tape(symbol: str, snapshot: Dict[str, Any]) -> Dict[str, Any]:
    raw = (snapshot.get("metadata") or {}).get(symbol) or {}
    if not raw:
        return {"source": "none", "available": False}
    avg_dvol = _f(raw.get("avg_dollar_vol_20"))
    price = _f(raw.get("price"))
    ret5 = _f(raw.get("return_5d_pct"))
    ret20 = _f(raw.get("return_20d_pct"))
    vol_ratio = _f(raw.get("volume_ratio_5d"), 1.0)
    stale = bool(raw.get("bars_stale"))
    confirmation = (
        price >= 5.0
        and avg_dvol >= 10_000_000
        and not stale
        and (vol_ratio >= 1.05 or ret5 >= 1.0 or ret20 >= 4.0)
    )
    return {
        "source": "universe_snapshot",
        "available": True,
        "price": round(price, 2),
        "avg_dollar_volume_20": round(avg_dvol, 2),
        "return_5d_pct": round(ret5, 2),
        "return_20d_pct": round(ret20, 2),
        "volume_ratio_5d": round(vol_ratio, 2),
        "bars_stale": stale,
        "confirmation": confirmation,
    }


def _fetch_yfinance_tape(symbols: Sequence[str], stats: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    if yf is None:
        stats["source_errors"].append("yfinance unavailable: import failed")
        return out
    for sym in symbols:
        try:
            tk = yf.Ticker(sym)
            hist = tk.history(period="1mo", interval="1d", auto_adjust=False)
            stats["api_attempts"]["yfinance_history"] = stats["api_attempts"].get("yfinance_history", 0) + 1
            if hist is None or hist.empty:
                continue
            closes = hist["Close"].dropna()
            vols = hist["Volume"].dropna()
            if closes.empty or vols.empty:
                continue
            price = float(closes.iloc[-1])
            prev5 = float(closes.iloc[-6]) if len(closes) >= 6 else float(closes.iloc[0])
            prev20 = float(closes.iloc[-21]) if len(closes) >= 21 else float(closes.iloc[0])
            avg_vol = float(vols.tail(20).mean() or 0.0)
            cur_vol = float(vols.iloc[-1] or 0.0)
            avg_dvol = avg_vol * price
            vol_ratio = cur_vol / avg_vol if avg_vol > 0 else 1.0
            ret5 = (price / prev5 - 1.0) * 100.0 if prev5 else 0.0
            ret20 = (price / prev20 - 1.0) * 100.0 if prev20 else 0.0
            out[sym] = {
                "source": "yfinance",
                "available": True,
                "price": round(price, 2),
                "avg_dollar_volume_20": round(avg_dvol, 2),
                "return_5d_pct": round(ret5, 2),
                "return_20d_pct": round(ret20, 2),
                "volume_ratio_5d": round(vol_ratio, 2),
                "bars_stale": False,
                "confirmation": (
                    price >= 5.0
                    and avg_dvol >= 10_000_000
                    and (vol_ratio >= 1.05 or ret5 >= 1.0 or ret20 >= 4.0)
                ),
            }
        except Exception as exc:
            stats["source_errors"].append(f"yfinance {sym} unavailable: {type(exc).__name__}")
    return out


def _profile_context(symbols: Sequence[str], stats: Dict[str, Any], use_fmp: bool) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    if not use_fmp:
        return out
    try:
        fmp = get_fmp()
    except Exception:
        return out
    for sym in symbols:
        try:
            profile = fmp.get_company_profile(sym) or {}
            stats["api_attempts"]["fmp_profile"] = stats["api_attempts"].get("fmp_profile", 0) + 1
            out[sym] = profile
        except Exception:
            out[sym] = {}
    return out


def _thirteen_f_context(symbols: Sequence[str], stats: Dict[str, Any], enabled: bool) -> Dict[str, Dict[str, Any]]:
    if not enabled:
        return {sym: {"available": False, "detail": "13F skipped"} for sym in symbols}
    try:
        from core.whale_tracker import get_whale_tracker

        tracker = get_whale_tracker()
    except Exception:
        tracker = None
    if tracker is None:
        return {sym: {"available": False, "detail": "13F unavailable"} for sym in symbols}
    out: Dict[str, Dict[str, Any]] = {}
    for sym in symbols:
        try:
            activity = tracker.get_institutional_activity(sym)
            stats["api_attempts"]["thirteen_f"] = stats["api_attempts"].get("thirteen_f", 0) + 1
            if activity:
                # Fix #5: the tracker returns a populated dict with
                # net_flow="UNKNOWN" when it completes a sweep but
                # 0/16 institutions returned data (transient SEC failure).
                # Surface that as "not available" so downstream scoring +
                # evidence don't treat UNKNOWN as a real reading.
                if str(activity.get("net_flow") or "").upper() == "UNKNOWN":
                    out[sym] = {
                        "available": False,
                        "detail": "13F sweep returned UNKNOWN (SEC transient)",
                    }
                else:
                    out[sym] = {
                        "available": True,
                        "net_flow": activity.get("net_flow"),
                        "confidence": activity.get("confidence"),
                        "whales_buying": activity.get("whales_buying"),
                        "whales_selling": activity.get("whales_selling"),
                        "last_quarter": activity.get("last_quarter"),
                        "detail": f"{activity.get('net_flow', 'UNKNOWN')} / {activity.get('confidence', 'UNKNOWN')}",
                    }
            else:
                out[sym] = {"available": False, "detail": "13F unavailable"}
        except Exception:
            out[sym] = {"available": False, "detail": "13F unavailable"}
    return out


def _tradier_context(symbols: Sequence[str], stats: Dict[str, Any], enabled: bool) -> Dict[str, Dict[str, Any]]:
    if not enabled:
        return {sym: {"available": False, "detail": "Tradier skipped"} for sym in symbols}
    # Use the shared Alpaca-first / Tradier-fallback chain. The legacy
    # token-only check is removed because Alpaca can serve options
    # without TRADIER_API_TOKEN; the chain's is_configured() now governs
    # whether we have any usable feed.
    try:
        from core.options_feed_factory import load_options_feed
        feed = load_options_feed()
    except Exception:
        feed = None
    if feed is None:
        return {sym: {"available": False, "detail": "Tradier unavailable: no token"} for sym in symbols}

    out: Dict[str, Dict[str, Any]] = {}
    for sym in symbols:
        try:
            if not getattr(feed, "is_configured", lambda: False)():
                out[sym] = {"available": False, "detail": "Tradier unavailable"}
                continue
            expirations = list(feed.get_expirations(sym) or [])
            stats["api_attempts"]["tradier_expirations"] = stats["api_attempts"].get("tradier_expirations", 0) + 1
            if not expirations:
                out[sym] = {"available": False, "detail": "Tradier chain unavailable"}
                continue
            chain = feed.get_chain(sym, expirations[0])
            stats["api_attempts"]["tradier_chain"] = stats["api_attempts"].get("tradier_chain", 0) + 1
            calls = (chain or {}).get("calls")
            puts = (chain or {}).get("puts")
            if calls is None or puts is None or calls.empty or puts.empty:
                out[sym] = {"available": False, "detail": "Tradier chain unavailable"}
                continue
            call_oi = float(calls["openInterest"].sum() or 0)
            put_oi = float(puts["openInterest"].sum() or 0)
            call_vol = float(calls["volume"].sum() or 0)
            put_vol = float(puts["volume"].sum() or 0)
            oi_tilt = (call_oi + 1.0) / (put_oi + 1.0)
            vol_tilt = (call_vol + 1.0) / (put_vol + 1.0)
            confirmation = oi_tilt >= 1.2 or vol_tilt >= 1.2
            out[sym] = {
                "available": True,
                "call_put_oi": round(oi_tilt, 2),
                "call_put_volume": round(vol_tilt, 2),
                "confirmation": confirmation,
                "detail": f"call/put oi {oi_tilt:.2f} | vol {vol_tilt:.2f}",
            }
        except Exception:
            out[sym] = {"available": False, "detail": "Tradier unavailable"}
    return out


def _score_tape(tape: Dict[str, Any]) -> float:
    if not tape.get("available"):
        return 0.0
    score = 0.0
    avg_dvol = _f(tape.get("avg_dollar_volume_20"))
    price = _f(tape.get("price"))
    ret5 = _f(tape.get("return_5d_pct"))
    ret20 = _f(tape.get("return_20d_pct"))
    vol = _f(tape.get("volume_ratio_5d"), 1.0)
    if price >= 8:
        score += 2.5
    if avg_dvol >= 100_000_000:
        score += 5.0
    elif avg_dvol >= 25_000_000:
        score += 3.5
    elif avg_dvol >= 10_000_000:
        score += 2.0
    if ret5 >= 1.0:
        score += 3.0
    elif ret5 >= -2.0:
        score += 1.0
    if ret20 >= 4.0:
        score += 2.5
    if vol >= 1.5:
        score += 3.0
    elif vol >= 1.05:
        score += 1.5
    if tape.get("bars_stale"):
        score -= 5.0
    return round(max(0.0, min(16.0, score)), 1)


def _noise_risk(noise_penalty: float) -> str:
    if noise_penalty >= 14:
        return "High"
    if noise_penalty >= 7:
        return "Medium"
    return "Low"


def _confidence_label(score: float) -> str:
    if score >= 78:
        return "High"
    if score >= 64:
        return "Medium"
    return "Watch"


# Minimum deterministic-score floor for Trends-corroborated XCONF promotion.
# A weak news lead (low credibility / weak mapping / borderline relevance)
# must already be in "Medium" confidence territory before Trends is allowed
# to lift it to Cross-Confirmed. This prevents Trends from rescuing junk.
TRENDS_XCONF_SCORE_FLOOR = 65.0


def _bucket_for_candidate(
    source_count: int,
    mapping_method: str,
    tape: Dict[str, Any],
    options: Dict[str, Any],
    specific_catalyst: bool,
    score: float,
    has_google_trends: bool = False,
    trend_z: Optional[float] = None,
    trend_z_threshold: float = 2.0,
) -> str:
    """Bucket assignment with explicit, asymmetric handling of Google Trends.

    XCONF paths (in order of strictness):
      (a) ≥2 independent news sources + tape_ok — the existing real-news path.
      (b) ≥1 independent news source + tape_ok + Trends spike (z ≥ threshold)
          + specific catalyst in the headline + score ≥ TRENDS_XCONF_SCORE_FLOOR.
          Trends is corroboration only and may NOT promote unless the lead is
          *already* a respectable News Catalyst on its own merits.

    `source_count` here is `non_trends_source_count` — the caller must ensure
    Trends is NOT counted as a source. (See `attach_trends_to_groups`.)
    """
    tape_ok = bool(tape.get("confirmation"))
    options_ok = bool(options.get("confirmation"))
    # Path (a): two real news sources + tape confirmation.
    if source_count >= 2 and tape_ok:
        return "Cross-Confirmed Lead"
    # Path (b): real-news + Trends corroboration, gated on score floor +
    # named catalyst + tape. Trends without a specific catalyst is not enough.
    trend_ok = (
        has_google_trends
        and isinstance(trend_z, (int, float))
        and float(trend_z) >= float(trend_z_threshold)
    )
    if (
        source_count >= 1
        and tape_ok
        and trend_ok
        and specific_catalyst
        and mapping_method != "theme_inference"
        and score >= TRENDS_XCONF_SCORE_FLOOR
    ):
        return "Cross-Confirmed Lead"
    if specific_catalyst and mapping_method != "theme_inference":
        return "News Catalyst"
    if (tape_ok or options_ok) and score >= 60:
        return "Options/Tape Confirmed"
    if mapping_method == "theme_inference":
        return "Emerging Theme"
    return "Watch Only / Needs Verification"


def score_candidates(
    groups: Dict[str, Dict[str, Any]],
    snapshot: Dict[str, Any],
    args: argparse.Namespace,
    stats: Dict[str, Any],
    drop_stats: Dict[str, Any],
) -> List[Candidate]:
    prelim = sorted(
        groups.values(),
        key=lambda g: (
            -float(g.get("mapping_confidence") or 0.0),
            float(g.get("freshness_hours") or 9999.0),
            str(g.get("ticker") or ""),
        ),
    )
    symbols = []
    for g in prelim:
        sym = str(g.get("ticker") or "").upper()
        if sym and sym not in symbols:
            symbols.append(sym)
    symbols = symbols[: max(args.tape_limit, args.limit)]

    metadata_tape = {sym: _metadata_tape(sym, snapshot) for sym in symbols}
    # Primary tape now Alpaca minute bars (free, in-house data) — fall back
    # to yfinance only if Alpaca didn't return rows for that symbol.
    alpaca_tape = (
        _alpaca_tape_context(symbols[: args.tape_limit], enabled=not getattr(args, "skip_alpaca_tape", False), stats=stats)
        if not getattr(args, "skip_alpaca_tape", False) else {}
    )
    yf_tape = {} if args.skip_yfinance else _fetch_yfinance_tape(symbols[: args.tape_limit], stats)
    profiles = _profile_context(symbols[: args.profile_limit], stats, use_fmp=not args.skip_fmp)
    thirteen_f = _thirteen_f_context(symbols[: args.overlay_limit], stats, enabled=not args.skip_13f)
    tradier = _tradier_context(symbols[: args.overlay_limit], stats, enabled=not args.skip_tradier)

    # New contexts (Tier 2 + 3): all cache-friendly or low-cost.
    internal_corr = _internal_corroboration_context(
        symbols, enabled=not getattr(args, "skip_internal_corroboration", False)
    )
    novelty_ctx = _novelty_context(symbols, enabled=not getattr(args, "skip_novelty_check", False))
    macro_windows = _macro_window_context(enabled=not getattr(args, "skip_macro_window", False))
    fmp_sent = _fmp_sentiment_context(
        symbols[: args.overlay_limit], enabled=not getattr(args, "skip_fmp_sentiment", False), stats=stats
    )
    insider_ctx = _insider_cluster_context(
        symbols[: args.overlay_limit], enabled=not getattr(args, "skip_insider", False), stats=stats
    )
    analyst_ctx = _analyst_changes_context(
        symbols[: args.overlay_limit], enabled=not getattr(args, "skip_analyst", False), stats=stats
    )

    alpha_set = _alpha_symbols()
    bte_set = _bte_symbols(snapshot)
    scanner_set = _scanner_symbols()

    candidates: List[Candidate] = []
    trend_z_threshold = float(getattr(args, "google_trends_z", 2.0))
    for group in prelim:
        sym = str(group["ticker"]).upper()
        titles = list(dict.fromkeys(group["titles"]))[:4]
        title_join = " | ".join(titles)
        # Source counts: Trends is a side-channel signal, not a source. The
        # attach helper guarantees `sources`/`source_types` exclude it, so
        # source_count == non_trends_source_count by construction. We keep
        # both names so the artifact contract is explicit for consumers.
        source_types_list = sorted(s for s in group["source_types"] if s != "google_trends")
        source_count = len({s for s in group["sources"]})
        non_trends_source_count = source_count
        source_type = "+".join(source_types_list)
        has_google_trends = bool(group.get("has_google_trends"))
        group_trend_z = group.get("trend_z")
        mapping_method = str(group["mapping_method"])
        mapping_conf = float(group["mapping_confidence"])
        theme = str(group["theme"] or "Unclassified")
        freshness = float(group["freshness_hours"])
        # Tape preference: Alpaca minute bars > yfinance daily > universe metadata.
        tape = alpaca_tape.get(sym) or yf_tape.get(sym) or metadata_tape.get(sym) or {"available": False, "source": "none"}
        profile = profiles.get(sym) or {}
        options = tradier.get(sym, {"available": False, "detail": "Tradier not checked"})
        inst = thirteen_f.get(sym, {"available": False, "detail": "13F not checked"})
        ic = internal_corr.get(sym) or {}
        nv = novelty_ctx.get(sym) or {}
        ins = insider_ctx.get(sym) or {"available": False}
        anal = analyst_ctx.get(sym) or {"available": False}
        fmp_sentiment_val = fmp_sent.get(sym)  # None if not fetched
        # Approximate the news timestamp as (now - freshness_hours).  Used to
        # check whether the lead lands inside a ±60min HIGH-impact macro window.
        news_ts_iso = ""
        _fh = group.get("freshness_hours")
        if isinstance(_fh, (int, float)):
            news_ts_iso = (datetime.now(timezone.utc) - timedelta(hours=float(_fh))).isoformat()
        macro_hit = _news_in_macro_window(news_ts_iso, macro_windows)
        noise_reasons: List[str] = list(dict.fromkeys(group.get("noise_terms") or []))
        specific_catalyst = any(term in title_join.lower() for term in CATALYST_TERMS)
        # #4 headline-subject sanity gate: the surfaced `news_label` is the
        # headline, so a ticker that is neither named in any headline nor the
        # FMP article subject is a body-only mention. Flag it as noise here so it
        # is penalised; the bucket is hard-capped to watch-only further below.
        subject_in_headline = bool(group.get("in_title")) or bool(group.get("is_subject"))
        if not subject_in_headline:
            noise_reasons.append("ticker not named in headline (body-only mention)")

        if tape.get("available"):
            price = _f(tape.get("price"))
            avg_dvol = _f(tape.get("avg_dollar_volume_20"))
            if price < 5.0:
                _drop(drop_stats, "low_price_or_junk_ticker", title_join, sym)
                continue
            if avg_dvol and avg_dvol < 7_500_000:
                _drop(drop_stats, "low_liquidity", title_join, sym)
                continue
            if tape.get("bars_stale"):
                _drop(drop_stats, "stale_tape", title_join, sym)
                continue
        elif mapping_method == "theme_inference":
            _drop(drop_stats, "theme_inference_without_tape", title_join, sym)
            continue
        if not tape.get("confirmation") and not options.get("confirmation"):
            _drop(drop_stats, "no_tape_confirmation", title_join, sym)
            continue

        mcap = _f(profile.get("marketCap"))
        if mcap and mcap < 250_000_000:
            _drop(drop_stats, "microcap_profile_filter", title_join, sym)
            continue

        if any(term in title_join.lower() for term in MEME_NOISE_TERMS):
            noise_reasons.append("meme/pump language")
        if mapping_method == "theme_inference" and not tape.get("confirmation"):
            noise_reasons.append("theme inference lacks tape confirmation")

        freshness_score = max(0.0, 18.0 - min(freshness, 168.0) / 168.0 * 18.0)
        credibility = max(_source_credibility("fmp_stock_news" if "fmp_stock_news" in group["source_types"] else "news_api", "", (group["urls"] or [""])[0]), 7.0)
        cross_source = 9.0 if source_count >= 2 else 3.0
        mapping_score = mapping_conf * 15.0
        novelty = float((THEME_RULES.get(theme) or {}).get("novelty") or 4.0)
        relevance = 12.0 if specific_catalyst else 8.0 if theme != "Unclassified" else 4.0
        tape_score = _score_tape(tape)
        options_score = 4.0 if options.get("confirmation") else 1.0 if options.get("available") else 0.0
        inst_score = 3.0 if str(inst.get("net_flow") or "").upper() == "BUYING" else 1.0 if inst.get("available") else 0.0

        # ── new score blocks ─────────────────────────────────────────────────
        # T2a — internal_corroboration: cross-check vs our alpha board + lens.
        ic_score = 0.0
        if ic.get("on_alpha_board"):
            tier = (ic.get("alpha_tier") or "").upper()
            ic_score += 5.0 if tier == "A" else 3.5 if tier == "B" else 2.5
            if ic.get("actionable_now"):
                ic_score += 1.5
        lc = ic.get("lens_composite")
        if isinstance(lc, (int, float)):
            if abs(lc) >= 0.30:
                ic_score += 2.0
            elif abs(lc) >= 0.15:
                ic_score += 1.0
        ic_score = min(ic_score, 8.0)

        # T2b — novelty_or_confirmation: zero-sum tag.  Confirmation is more
        # valuable than novelty (we already have a view to validate against).
        if nv.get("has_recent_activity"):
            novelty_tag = "confirmation"
            novelty_score = 4.0
        else:
            novelty_tag = "novelty"
            novelty_score = 1.0  # small positive — fresh radar leads have value

        # T3a — FMP sentiment_score (0=bearish, 1=bullish, 0.5=neutral).
        # Reward conviction in either direction; punish only fence-sitting.
        if isinstance(fmp_sentiment_val, (int, float)):
            sentiment_score_block = round(abs(float(fmp_sentiment_val) - 0.5) * 6.0, 1)  # max 3.0
        else:
            sentiment_score_block = 0.0

        # T3b — insider cluster.  Cluster-buy is rare and high-precision.
        insider_score = 0.0
        if ins.get("available"):
            sig = ins.get("cluster_signal")
            if sig == "cluster_buy":
                insider_score = 5.0
            elif sig == "cluster_sell":
                insider_score = -3.0  # negative → suppresses score
            elif int(ins.get("distinct_buyers_14d") or 0) >= 1:
                insider_score = 1.0

        # T3d — analyst grade momentum (last 21d).
        analyst_score = 0.0
        if anal.get("available"):
            ups = int(anal.get("recent_upgrades_21d") or 0)
            downs = int(anal.get("recent_downgrades_21d") or 0)
            if ups >= 2 and ups > downs:
                analyst_score = 3.0
            elif downs >= 2 and downs > ups:
                analyst_score = -2.0
            elif ups + downs > 0:
                analyst_score = 0.5

        noise_penalty = 0.0
        if noise_reasons:
            noise_penalty += 8.0
        if mapping_method == "theme_inference":
            noise_penalty += 5.0
        if theme == "Unclassified":
            noise_penalty += 4.0
        if source_count == 1 and mapping_method == "theme_inference":
            noise_penalty += 4.0
        if not tape.get("confirmation") and not specific_catalyst:
            noise_penalty += 5.0
        # T2c — leads landing in a ±60 min HIGH-impact macro window are
        # almost always noise (market reaction is macro-driven, not
        # ticker-specific).  Down-weight rather than hard-drop.
        if macro_hit is not None:
            noise_penalty += 6.0
            noise_reasons.append(f"macro_window:{macro_hit['event'][:36]}")

        total = (
            freshness_score
            + credibility
            + cross_source
            + mapping_score
            + novelty
            + relevance
            + tape_score
            + options_score
            + inst_score
            + ic_score
            + novelty_score
            + sentiment_score_block
            + insider_score
            + analyst_score
            - noise_penalty
        )
        total = round(max(0.0, min(100.0, total)), 1)
        if total < args.quality_floor:
            _drop(drop_stats, "below_quality_floor", title_join, sym)
            continue
        if _noise_risk(noise_penalty) == "High":
            _drop(drop_stats, "noise_risk_high", title_join, sym)
            continue

        bucket = _bucket_for_candidate(
            non_trends_source_count,
            mapping_method,
            tape,
            options,
            specific_catalyst,
            total,
            has_google_trends=has_google_trends,
            trend_z=(float(group_trend_z) if isinstance(group_trend_z, (int, float)) else None),
            trend_z_threshold=trend_z_threshold,
        )
        # #4 hard cap: a body-only mention can never be promoted to a real
        # catalyst/lead bucket — its headline is not about this ticker.
        if not subject_in_headline:
            bucket = "Watch Only / Needs Verification"
        cross_refs = []
        if sym in alpha_set:
            cross_refs.append("ALPHA+")
        if sym in bte_set:
            cross_refs.append("POSTURE+")
        if sym in scanner_set:
            cross_refs.append("SCANNER+")
        if ic.get("on_alpha_board") and (ic.get("alpha_tier") or "").upper() == "A":
            cross_refs.append("ALPHA-A")
        if novelty_tag == "confirmation":
            cross_refs.append("CONFIRMS")
        if (ins.get("cluster_signal") or "") == "cluster_buy":
            cross_refs.append("INSIDER+")
        if int(anal.get("recent_upgrades_21d") or 0) >= 2:
            cross_refs.append("ANALYST+")
        if group.get("trend_z") is not None:
            cross_refs.append("TRENDS+")

        evidence = [
            f"{source_count} source(s): {', '.join(sorted(group['sources'])[:3])}",
            f"mapping: {', '.join(sorted(group['mapping_labels']))}",
        ]
        if tape.get("available"):
            evidence.append(
                f"tape {tape.get('source')}: 5d {_f(tape.get('return_5d_pct')):+.1f}% "
                f"20d {_f(tape.get('return_20d_pct')):+.1f}% vol {_f(tape.get('volume_ratio_5d'), 1.0):.2f}x"
            )
        if options.get("available"):
            evidence.append(f"options: {options.get('detail')}")
        if inst.get("available"):
            evidence.append(f"13F: {inst.get('detail')}")
        if ic.get("on_alpha_board"):
            evidence.append(
                f"alpha board: tier={ic.get('alpha_tier','?')} score={_f(ic.get('alpha_score')):.0f} "
                f"actionable={bool(ic.get('actionable_now'))}"
            )
        if isinstance(ic.get("lens_composite"), (int, float)):
            evidence.append(
                f"lens: composite {ic.get('lens_composite'):+.2f} · {ic.get('lens_label','')}"
            )
        if novelty_tag == "confirmation":
            evidence.append(
                f"paper_signals: {int(nv.get('recent_signal_count') or 0)} recent / "
                f"{int(nv.get('recent_decision_count') or 0)} decisions in last 14d"
            )
        if isinstance(fmp_sentiment_val, (int, float)):
            evidence.append(f"FMP sentiment: {fmp_sentiment_val:.2f} (0=bear, 1=bull)")
        if ins.get("available"):
            evidence.append(f"insider 14d: {ins.get('detail')}")
        if anal.get("available"):
            evidence.append(f"analyst 21d: {anal.get('detail')}")
        if macro_hit is not None:
            evidence.append(f"⚠ macro window hit: {macro_hit['event']}")
        if group.get("trend_z") is not None:
            _tz = float(group.get("trend_z") or 0.0)
            _tl = group.get("trend_latest")
            _tl_txt = (
                f" @ relative index {int(_tl)}/100"
                if isinstance(_tl, (int, float))
                else ""
            )
            evidence.append(
                f"Relative Google Trends spike vs 7d baseline: z={_tz:+.2f}{_tl_txt} "
                f"(corroboration only)"
            )

        missing = []
        if not subject_in_headline:
            missing.append("ticker not named in the headline — verify the article is actually about this ticker")
        if mapping_method == "theme_inference":
            missing.append("direct company-specific catalyst")
        if not tape.get("confirmation"):
            missing.append("stronger tape confirmation")
        if not options.get("available"):
            missing.append("options participation overlay")
        if not inst.get("available"):
            missing.append("current 13F context")
        if not ic.get("on_alpha_board"):
            missing.append("alpha-board cross-validation")
        if novelty_tag == "novelty":
            missing.append("no recent paper_signals activity — purely net-new")
        if not ins.get("available"):
            missing.append("insider transaction history")

        why = _why_it_matters(sym, theme, bucket, titles[0], tape)
        manual = _manual_check(sym, theme, mapping_method, options, inst)
        candidates.append(
            Candidate(
                id=_stable_id(sym, titles[0], theme),
                ticker=sym,
                bucket=bucket,
                confidence=_confidence_label(total),
                confidence_score=total,
                noise_risk=_noise_risk(noise_penalty),
                source_type=source_type,
                theme=theme,
                news_label=_clip(titles[0], 90),
                why_it_matters=why,
                manual_check_needed=manual,
                cross_refs=cross_refs,
                deterministic_score=total,
                anthropic_verdict="NOT_RUN",
                evidence_supporting=evidence[:10],
                evidence_missing=missing[:7],
                source_count=source_count,
                source_titles=titles,
                urls=list(dict.fromkeys(group["urls"]))[:4],
                mapping_method=mapping_method,
                mapping_confidence=round(mapping_conf, 2),
                freshness_hours=round(freshness, 2),
                tape=tape,
                options=options,
                thirteen_f=inst,
                score_blocks={
                    "freshness": round(freshness_score, 1),
                    "source_credibility": round(credibility, 1),
                    "cross_source_confirmation": round(cross_source, 1),
                    "ticker_mapping": round(mapping_score, 1),
                    "theme_novelty": round(novelty, 1),
                    "market_relevance": round(relevance, 1),
                    "tape_confirmation": round(tape_score, 1),
                    "options_confirmation": round(options_score, 1),
                    "institutional_background": round(inst_score, 1),
                    "internal_corroboration": round(ic_score, 1),
                    "novelty_or_confirmation": round(novelty_score, 1),
                    "fmp_sentiment": round(sentiment_score_block, 1),
                    "insider_cluster": round(insider_score, 1),
                    "analyst_changes": round(analyst_score, 1),
                    "noise_penalty": round(noise_penalty, 1),
                },
                noise_reasons=noise_reasons[:6],
                trend_signal=(
                    {
                        "z": round(float(group["trend_z"]), 2),
                        "latest": group.get("trend_latest"),
                        "source": "google_trends",
                        "is_corroboration_only": True,
                        "latest_is_relative_index": True,
                    }
                    if group.get("trend_z") is not None
                    else {}
                ),
                source_types=source_types_list,
                non_trends_source_count=non_trends_source_count,
                has_google_trends=has_google_trends,
                trend_z=(
                    round(float(group_trend_z), 2)
                    if isinstance(group_trend_z, (int, float))
                    else None
                ),
                trend_is_corrob_only=True,
            )
        )

    candidates.sort(key=lambda c: (-c.deterministic_score, c.noise_risk, c.ticker))
    # Per-ticker cap — preserve variety on busy news days. A single ticker
    # (typically a mega-cap) can otherwise grab 8/10 visible slots on a
    # high-volume theme run.
    cap = max(1, int(getattr(args, "per_ticker_cap", 2) or 2))
    seen_per_ticker: Dict[str, int] = {}
    capped: List[Candidate] = []
    for c in candidates:
        if seen_per_ticker.get(c.ticker, 0) >= cap:
            _drop(drop_stats, "per_ticker_cap_reached", c.news_label, c.ticker)
            continue
        seen_per_ticker[c.ticker] = seen_per_ticker.get(c.ticker, 0) + 1
        capped.append(c)
    return capped[: args.limit]


def _validated_theme_label(symbol: str, theme: str, title: str) -> str:
    """Return theme only when it is actually evidenced by the headline text.

    Falls back to 'Company-specific' when the theme was inferred from the
    article body/description but the title doesn't contain the theme's terms
    and the ticker is not a primary expected ticker for that theme.  This
    prevents mislabels like 'Crypto headline' for a JPMorgan equity-issuance
    article that happened to mention 'digital assets' in its body text.

    Single-word terms use word-boundary matching to prevent false matches
    like "chip" firing on "Chipotle".  Multi-word phrases use exact substring.
    """
    rule = THEME_RULES.get(theme)
    if not rule:
        return "Company-specific"
    title_low = title.lower()
    for term in rule.get("terms", []):
        if " " in term:
            if term in title_low:
                return theme
        else:
            if re.search(rf"\b{re.escape(term)}\b", title_low):
                return theme
    if symbol in (rule.get("tickers") or []):
        return theme
    return "Company-specific"


def _why_it_matters(symbol: str, theme: str, bucket: str, title: str, tape: Dict[str, Any]) -> str:
    if bucket == "News Catalyst":
        return _clip(f"Company-specific catalyst may change investor expectations: {title}", 170)
    if bucket == "Cross-Confirmed Lead":
        return _clip(f"Story is showing both source confirmation and tape participation in {symbol}.", 170)
    if bucket == "Options/Tape Confirmed":
        theme_label = _validated_theme_label(symbol, theme, title)
        return _clip(f"{theme_label} headline has participation confirmation; check whether move is early or already crowded.", 170)
    if bucket == "Emerging Theme":
        theme_label = _validated_theme_label(symbol, theme, title)
        return _clip(f"{theme_label} theme is appearing in news flow; ticker link is inferential and needs confirmation.", 170)
    return _clip(f"Interesting {theme} setup, but evidence is incomplete and should stay watch-only.", 170)


def _manual_check(symbol: str, theme: str, mapping_method: str, options: Dict[str, Any], inst: Dict[str, Any]) -> str:
    checks = ["verify the original article and timestamp"]
    if mapping_method == "theme_inference":
        checks.append("confirm the ticker is a real beneficiary, not just a theme proxy")
    checks.append("check latest tape and intraday volume")
    if not options.get("available"):
        checks.append("check options OI/volume manually if relevant")
    if not inst.get("available"):
        checks.append("treat 13F as missing background only")
    return "; ".join(checks[:4])


def _load_regime_context() -> Dict[str, Any]:
    """Load current regime from cache so Claude can calibrate its conservatism."""
    try:
        path = ROOT / "cache" / "research" / "regime_forecast_latest.json"
        data = _load_json(path)
        if not data:
            return {}
        head = data.get("headline") or {}
        sr = data.get("sector_rotation") or {}
        return {
            "regime": head.get("current_regime", "Unknown"),
            "confidence": head.get("confidence", "unknown"),
            "bias_5d": head.get("bias_5d", "unknown"),
            "leading_sectors": (sr.get("leading") or [])[:4],
            "weakening_sectors": (sr.get("weakening") or [])[:4],
        }
    except Exception:
        return {}


def _anthropic_prompt(candidates: Sequence[Candidate], regime_ctx: Dict[str, Any]) -> str:
    regime_block = ""
    if regime_ctx:
        leading = ", ".join(regime_ctx.get("leading_sectors") or []) or "none"
        weakening = ", ".join(regime_ctx.get("weakening_sectors") or []) or "none"
        regime_block = (
            f"\nMarket context (use to calibrate DROP threshold):\n"
            f"  Regime: {regime_ctx.get('regime', 'Unknown')} "
            f"(confidence: {regime_ctx.get('confidence', '?')}, "
            f"5d bias: {regime_ctx.get('bias_5d', '?')})\n"
            f"  Leading sectors: {leading}\n"
            f"  Weakening sectors: {weakening}\n"
            f"In weakening regimes or when sector is weakening, raise the bar for KEEP.\n"
        )

    payload = [
        {
            "id": c.id,
            "ticker": c.ticker,
            "bucket": c.bucket,
            "score": c.deterministic_score,
            "noise_risk": c.noise_risk,
            "theme": c.theme,
            "news_label": c.news_label,
            "mapping_method": c.mapping_method,
            "freshness_hours": round(c.freshness_hours, 1),
            "evidence_supporting": c.evidence_supporting,
            "evidence_missing": c.evidence_missing,
            "source_titles": c.source_titles[:3],
            "tape": c.tape,
            "options": c.options,
            "thirteen_f": c.thirteen_f,
        }
        for c in candidates
    ]

    instructions = (
        "You are a conservative market research analyst filtering social/news arb signals.\n"
        "Your primary job is aggressive noise removal — most candidates should be dropped.\n\n"
        "VERDICT DEFINITIONS:\n"
        "  KEEP  — ticker-specific, verifiable catalyst; tape or options confirm participation;\n"
        "          not stale (freshness_hours < 36); not generic sector noise; worth human review NOW.\n"
        "  DROP  — catalyst exists but is too vague, already priced in, or the ticker link is weak.\n"
        "  NOISE — headline is not specifically about this ticker; pure sector/macro news;\n"
        "          meme/hype language; or freshness_hours > 48 with no tape confirmation.\n\n"
        "Default to DROP. Only KEEP if you can write a crisp one-sentence thesis.\n"
        "Aim for 2–4 KEEPs out of 10 candidates at most.\n\n"
        "Return strict JSON with top-level key 'reviews'. Each review must have:\n"
        "  id, verdict (KEEP|DROP|NOISE), ticker, theme,\n"
        "  why_it_may_matter (KEEP only: one sentence; empty string otherwise),\n"
        "  evidence_supporting (list, 1-3 specific data-backed facts),\n"
        "  evidence_missing (list, 1-3 things that would raise conviction),\n"
        "  noise_risk (Low|Medium|High),\n"
        "  time_sensitivity (BREAKING if freshness_hours<2 | RECENT if 2-24h | DATED if 24-48h | EXPIRED if >48h),\n"
        "  confidence_pct (integer 0-100: your confidence a human review of this lead is worthwhile),\n"
        "  manual_verification_step (one specific action to verify; empty string if NOISE),\n"
        "  concise_thesis (one sentence for the operator if KEEP; empty string if DROP/NOISE).\n\n"
        "No trade approvals. No paper signals. No entry/exit prices. Research-only."
    )

    return (
        instructions
        + regime_block
        + "\n\nCandidates:\n"
        + json.dumps({"candidates": payload}, indent=2)
    )


def anthropic_review(candidates: Sequence[Candidate], args: argparse.Namespace, stats: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    review_count = min(len(candidates), max(0, min(args.anthropic_limit, DEFAULT_REVIEW_LIMIT)))
    if review_count <= 0:
        stats["anthropic"] = {"called": False, "reason": "no candidates after deterministic filtering", "review_count": 0}
        return {}
    if args.skip_anthropic:
        stats["anthropic"] = {"called": False, "reason": "skipped by flag", "review_count": 0}
        return {}
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        stats["anthropic"] = {"called": False, "reason": "ANTHROPIC_API_KEY not set", "review_count": 0}
        return {}
    try:
        import anthropic
    except Exception:
        stats["anthropic"] = {"called": False, "reason": "anthropic package unavailable", "review_count": 0}
        return {}

    top = list(candidates)[:review_count]
    regime_ctx = _load_regime_context()
    try:
        client = anthropic.Anthropic(api_key=api_key)
        # Full dated model ID to avoid silent fallback to older versions.
        # Override via SOCIAL_ARB_ANTHROPIC_MODEL env var.
        model = os.getenv("SOCIAL_ARB_ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
        # max_tokens budget: each review ≈ 600–800 tokens; 10 reviews need ~7–8K.
        msg = client.messages.create(
            model=model,
            max_tokens=9000,
            temperature=0,
            system=(
                "You are a conservative market research analyst. This is research-only. "
                "You never approve trades, generate entry/exit prices, create paper signals, "
                "or trigger any automated action. Output must be machine-parseable JSON."
            ),
            messages=[{"role": "user", "content": _anthropic_prompt(top, regime_ctx)}],
        )
        stats["api_attempts"]["anthropic_messages"] = stats["api_attempts"].get("anthropic_messages", 0) + 1
        text = ""
        for block in msg.content:
            text += getattr(block, "text", "") or ""
        parsed = _extract_json_object(text)
        reviews = parsed.get("reviews") if isinstance(parsed, dict) else None
        if not isinstance(reviews, list):
            reviews = []
        out = {
            str(r.get("id")): r
            for r in reviews
            if isinstance(r, dict) and r.get("id")
        }
        stats["anthropic"] = {"called": True, "model": model, "review_count": review_count, "parsed_reviews": len(out)}
        return out
    except Exception as exc:
        stats["anthropic"] = {"called": False, "reason": f"Anthropic failed: {type(exc).__name__}: {exc}", "review_count": 0}
        return {}


def _extract_json_object(text: str) -> Dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?", "", stripped).strip()
        stripped = re.sub(r"```$", "", stripped).strip()
    try:
        return json.loads(stripped)
    except Exception:
        pass
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(stripped[start : end + 1])
        except Exception:
            return {}
    return {}


def apply_reviews(
    candidates: Sequence[Candidate],
    reviews: Dict[str, Dict[str, Any]],
    drop_stats: Dict[str, Any],
) -> List[Candidate]:
    if not reviews:
        return list(candidates)
    visible: List[Candidate] = []
    for cand in candidates:
        review = reviews.get(cand.id)
        if not review:
            visible.append(cand)
            continue
        verdict = str(review.get("verdict") or "").upper().strip()
        cand.anthropic_verdict = verdict or "UNPARSED"
        if review.get("why_it_may_matter"):
            cand.why_it_matters = _clip(review.get("why_it_may_matter"), 170)
        if review.get("manual_verification_step"):
            cand.manual_check_needed = _clip(review.get("manual_verification_step"), 190)
        if review.get("concise_thesis"):
            cand.news_label = _clip(review.get("concise_thesis"), 95)
        if isinstance(review.get("evidence_supporting"), list):
            cand.evidence_supporting = [str(x) for x in review["evidence_supporting"][:5]]
        if isinstance(review.get("evidence_missing"), list):
            cand.evidence_missing = [str(x) for x in review["evidence_missing"][:5]]
        ts = str(review.get("time_sensitivity") or "").upper().strip()
        if ts in {"BREAKING", "RECENT", "DATED", "EXPIRED"}:
            cand.time_sensitivity = ts
        raw_conf = review.get("confidence_pct")
        if isinstance(raw_conf, (int, float)):
            cand.confidence_pct = max(0, min(100, int(raw_conf)))
        if verdict in {"DROP", "NOISE"}:
            _drop(drop_stats, f"anthropic_{verdict.lower()}", " | ".join(cand.source_titles), cand.ticker)
            continue
        visible.append(cand)
    return visible


def _render_text(artifact: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append("SOCIAL ARB RADAR")
    lines.append("research-only; twice-weekly; not paper evidence; not trade approval")
    lines.append("")
    lines.append(f"version: {artifact.get('version')}  mode: {artifact.get('mode')}  built_at: {artifact.get('built_at')}")
    lines.append(f"raw_items: {artifact.get('raw_item_count', 0)}  normalized: {artifact.get('normalized_item_count', 0)}  dropped: {(artifact.get('dropped_noise') or {}).get('total', 0)}")
    anth = artifact.get("anthropic") or {}
    lines.append(f"anthropic: called={anth.get('called')} review_count={anth.get('review_count', 0)} reason={anth.get('reason', '')}")
    lines.append("")
    items = artifact.get("items") or []
    if not items:
        lines.append("No high-quality social/news arb leads this run.")
    else:
        for i, row in enumerate(items, 1):
            markers = " ".join(row.get("cross_refs") or []) or "-"
            verdict = row.get("anthropic_verdict", "NOT_RUN")
            cconf = row.get("confidence_pct")
            tsens = row.get("time_sensitivity", "UNKNOWN")
            claude_tag = ""
            if verdict not in ("NOT_RUN", ""):
                claude_tag = f"  claude={verdict}"
                if cconf is not None:
                    claude_tag += f"({cconf}%)"
                if tsens and tsens != "UNKNOWN":
                    claude_tag += f"  timing={tsens}"
            lines.append(
                f"{i}. {row.get('ticker')}  {row.get('bucket')}  "
                f"conf {row.get('confidence')}({row.get('confidence_score')})  "
                f"noise {row.get('noise_risk')}  {markers}{claude_tag}"
            )
            lines.append(f"   label: {row.get('news_label')}")
            lines.append(f"   why: {row.get('why_it_matters')}")
            lines.append(f"   check: {row.get('manual_check_needed')}")
            lines.append("")
    dropped = artifact.get("dropped_noise") or {}
    reasons = dropped.get("reasons") or {}
    if reasons:
        lines.append("Dropped / Noise")
        for reason, count in sorted(reasons.items(), key=lambda kv: (-int(kv[1]), kv[0])):
            lines.append(f"  {reason}: {count}")
    return "\n".join(lines).rstrip() + "\n"


def _save_artifacts(artifact: Dict[str, Any], raw_payload: Dict[str, Any]) -> Dict[str, str]:
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    cfg.LOG_DIR.mkdir(parents=True, exist_ok=True)
    SOCIAL_RAW_PATH.write_text(json.dumps(raw_payload, indent=2), encoding="utf-8")
    artifact["artifacts"] = {
        "json": str(SOCIAL_JSON_PATH),
        "raw": str(SOCIAL_RAW_PATH),
        "text": str(SOCIAL_TEXT_PATH),
    }
    SOCIAL_JSON_PATH.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    SOCIAL_TEXT_PATH.write_text(_render_text(artifact), encoding="utf-8")
    return artifact["artifacts"]


def _fresh_cached_artifact(max_age_hours: float) -> Optional[Dict[str, Any]]:
    data = _load_json(SOCIAL_JSON_PATH)
    if not data:
        return None
    built = _parse_dt(data.get("built_at"))
    if built is None:
        return None
    if _age_hours(built) <= max_age_hours:
        return data
    return None


def build_radar(args: argparse.Namespace) -> Dict[str, Any]:
    stats: Dict[str, Any] = {
        "api_attempts": {},
        "source_status": {},
        "source_errors": [],
        "anthropic": {},
    }
    drop_stats: Dict[str, Any] = {"total": 0, "reasons": {}, "examples": []}

    snapshot = _load_universe_snapshot(args.snapshot_path)
    known = _known_symbols(snapshot)
    raw_items = collect_raw_items(args, stats)
    normalized = normalize_items(raw_items, known)
    groups, trend_items = build_story_groups(normalized, known, drop_stats)
    attach_trends_to_groups(
        groups,
        trend_items,
        stats,
        max_age_hours=float(getattr(args, "google_trends_max_age_hours", 72.0)),
    )
    candidates = score_candidates(groups, snapshot, args, stats, drop_stats)
    reviews = anthropic_review(candidates, args, stats)
    reviewed = apply_reviews(candidates, reviews, drop_stats)
    reviewed.sort(key=lambda c: (-c.deterministic_score, c.noise_risk, c.ticker))
    visible = reviewed[: min(DEFAULT_VISIBLE_LIMIT, args.limit)]

    artifact = {
        "version": VERSION,
        "mode": args.mode,
        "built_at": _utc_now().isoformat(),
        "sample_data": bool(args.offline_sample),
        "cadence": {
            "default": "twice_weekly",
            "recommended": ["Tuesday after market close", "Thursday or Sunday after market close"],
            "dashboard_policy": "cache-only; no provider calls on render",
            "min_run_interval_hours": DEFAULT_MIN_RUN_INTERVAL_HOURS,
        },
        "guardrails": [
            "research-only",
            "not trade approval",
            "not paper evidence",
            "not sleeve approval",
            "no Alpha Discovery scoring changes",
            "cache-first",
            "hard noise filtering before Anthropic",
        ],
        "sources_used": {
            "news_api": stats["source_status"].get("news_api", "not used"),
            "fmp": stats["source_status"].get("fmp_news", "not used"),
            "alpaca_news": stats["source_status"].get("alpaca_news", "not used"),
            "google_trends": stats["source_status"].get("google_trends", "not used"),
            "alpaca_tape": "minute bars (primary)" if not getattr(args, "skip_alpaca_tape", False) else "skipped by flag",
            "yfinance": "fallback tape only" if not args.skip_yfinance else "skipped by flag",
            "thirteen_f": "top candidates only" if not args.skip_13f else "skipped by flag",
            "tradier": "top candidates only" if not args.skip_tradier else "skipped by flag",
            "fmp_sentiment": "top candidates only" if not getattr(args, "skip_fmp_sentiment", False) else "skipped by flag",
            "fmp_insider": "top candidates only" if not getattr(args, "skip_insider", False) else "skipped by flag",
            "fmp_analyst_grades": "top candidates only" if not getattr(args, "skip_analyst", False) else "skipped by flag",
            "alpha_board": "internal corroboration" if not getattr(args, "skip_internal_corroboration", False) else "skipped by flag",
            "stock_lens": "internal corroboration" if not getattr(args, "skip_internal_corroboration", False) else "skipped by flag",
            "paper_signals_db": "novelty/confirmation tag" if not getattr(args, "skip_novelty_check", False) else "skipped by flag",
            "macro_calendar": "veto window" if not getattr(args, "skip_macro_window", False) else "skipped by flag",
            "anthropic": stats.get("anthropic", {}),
        },
        "methodology": {
            "collect": "FMP stock news and News API when configured; raw payloads cached",
            "normalize": "title/source/timestamp/source_type/tickers/company aliases/theme/freshness/url",
            "deduplicate": "ticker plus normalized story key, source sets retained",
            "map_to_tickers": "direct ticker > company alias > labeled theme inference",
            "hard_noise_filter": [
                "stale source",
                "generic/non-actionable news",
                "listicle / clickbait headlines (e.g. 'X% Over a Decade', 'Forget X. Buy Y', 'Mag 7', '5 Best Stocks')",
                "no public-company mapping",
                "low liquidity or low price",
                "theme inference without tape confirmation",
                "high meme/pump noise risk",
            ],
            "deterministic_scoring": [
                "freshness",
                "source credibility",
                "cross-source confirmation",
                "ticker mapping confidence",
                "theme novelty",
                "market relevance",
                "tape confirmation",
                "options confirmation",
                "13F background",
                "internal corroboration (alpha board + stock lens)",
                "novelty vs confirmation (paper_signals lookback 14d)",
                "FMP sentiment score",
                "insider cluster (14d buy/sell)",
                "analyst grade changes (21d)",
                "macro-window down-weight (±60min HIGH-impact events)",
                "noise-risk penalty",
            ],
            "anthropic_contract": {
                "review_cap": min(args.anthropic_limit, DEFAULT_REVIEW_LIMIT),
                "only_after_deterministic_filtering": True,
                "allowed_verdicts": ["KEEP", "DROP", "NOISE"],
                "fields": [
                    "ticker/theme",
                    "why it may matter",
                    "evidence supporting it",
                    "evidence missing",
                    "noise risk",
                    "manual verification step",
                    "concise thesis",
                ],
            },
        },
        "future_data_sources_to_investigate": [
            "Google Trends API / early access",
            "YouTube Data API metadata",
            "TikTok Research API if eligible",
            "app ranking / product-review / web-traffic datasets",
            "FMP /stable/senate-trading + /stable/house-trading (politician trades)",
            "FMP /stable/short-interest (squeeze setup detection)",
            "FMP /stable/press-releases (cleaner than third-party news)",
            "FMP /stable/sec-filings (8-K event flags)",
            "Tradier unusual options activity (sweeps + blocks)",
        ],
        "raw_item_count": len(raw_items),
        "normalized_item_count": len(normalized),
        "story_group_count": len(groups),
        "candidate_count": len(candidates),
        "visible_count": len(visible),
        "anthropic": stats.get("anthropic", {}),
        "items": [c.to_dict() for c in visible],
        "reviewed_candidates": [c.to_dict() for c in reviewed[: args.limit]],
        "dropped_noise": drop_stats,
        "usage": stats,
        "source_errors": stats["source_errors"][:20],
    }
    raw_payload = {
        "version": VERSION,
        "built_at": artifact["built_at"],
        "raw_items": raw_items,
        "normalized_items": [asdict(item) for item in normalized],
    }
    _save_artifacts(artifact, raw_payload)
    return artifact


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Smart Social Arb Radar V1")
    parser.add_argument("--mode", choices=("twice_weekly", "daily"), default="twice_weekly")
    parser.add_argument("--limit", type=int, default=20, help="maximum candidates retained in artifacts")
    parser.add_argument("--quality-floor", type=float, default=50.0)
    parser.add_argument("--anthropic-limit", type=int, default=DEFAULT_REVIEW_LIMIT)
    parser.add_argument("--min-run-interval-hours", type=float, default=DEFAULT_MIN_RUN_INTERVAL_HOURS)
    parser.add_argument("--tape-limit", type=int, default=40)
    parser.add_argument("--profile-limit", type=int, default=24)
    parser.add_argument(
        "--overlay-limit",
        type=int,
        default=5,
        help="Number of top candidates that receive heavy overlay lookups "
             "(13F via SEC EDGAR, Tradier options chain, FMP sentiment / "
             "insider / analyst).  Lower = less provider pressure and faster "
             "cold-cache runs; higher = deeper overlay coverage.  Default 5.",
    )
    parser.add_argument("--snapshot-path", type=Path, default=None)
    parser.add_argument("--force", action="store_true", help="ignore twice-weekly minimum interval guard")
    parser.add_argument("--offline-sample", action="store_true", help="use deterministic local sample data for smoke tests")
    parser.add_argument("--skip-fmp", action="store_true")
    parser.add_argument("--skip-newsapi", action="store_true")
    parser.add_argument("--skip-yfinance", action="store_true")
    parser.add_argument("--skip-13f", action="store_true")
    parser.add_argument("--skip-tradier", action="store_true")
    parser.add_argument("--skip-anthropic", action="store_true")
    parser.add_argument(
        "--per-ticker-cap",
        type=int,
        default=2,
        help="Max candidates retained per ticker after scoring. Prevents a "
             "single ticker (e.g. AAPL) from crowding out the visible roster "
             "on a busy news day.",
    )
    parser.add_argument("--skip-internal-corroboration", action="store_true",
                        help="Skip alpha/lens internal cross-validation lookup.")
    parser.add_argument("--skip-novelty-check", action="store_true",
                        help="Skip paper_signals novelty-vs-confirmation tagging.")
    parser.add_argument("--skip-macro-window", action="store_true",
                        help="Skip ±60min HIGH-impact macro window down-weighting.")
    parser.add_argument("--skip-fmp-sentiment", action="store_true",
                        help="Skip FMP sentiment_score lookup.")
    parser.add_argument("--skip-insider", action="store_true",
                        help="Skip insider-trading cluster-buy lookup.")
    parser.add_argument("--skip-analyst", action="store_true",
                        help="Skip analyst upgrades/downgrades lookup.")
    parser.add_argument("--skip-alpaca-tape", action="store_true",
                        help="Skip Alpaca minute-bar tape (falls back to yfinance).")
    parser.add_argument("--skip-alpaca-news", action="store_true",
                        help="Skip Alpaca News API as a third news source.")
    parser.add_argument("--skip-google-trends", action="store_true",
                        help="Skip Google Trends (pytrends) interest-spike source.")
    parser.add_argument("--google-trends-limit", type=int, default=30,
                        help="Max number of curated tickers checked against Google Trends per run.")
    parser.add_argument("--google-trends-z", type=float, default=2.0,
                        help="Z-score threshold over 7d baseline to flag a Trends spike.")
    parser.add_argument("--google-trends-max-age-hours", type=float, default=72.0,
                        help="Max age of a news group that can absorb a Trends spike (default 72h covers weekend cycles).")
    parser.add_argument("--json", action="store_true", help="print JSON instead of text summary")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    args.limit = max(1, min(50, int(args.limit)))
    args.anthropic_limit = max(0, min(DEFAULT_REVIEW_LIMIT, int(args.anthropic_limit)))
    args.tape_limit = max(0, min(80, int(args.tape_limit)))
    args.profile_limit = max(0, min(60, int(args.profile_limit)))
    args.overlay_limit = max(0, min(20, int(args.overlay_limit)))
    args.per_ticker_cap = max(1, min(10, int(getattr(args, "per_ticker_cap", 2))))

    if args.mode == "twice_weekly" and not args.force and not args.offline_sample:
        cached = _fresh_cached_artifact(args.min_run_interval_hours)
        if cached:
            if args.json:
                print(json.dumps(cached, indent=2))
            else:
                print(_render_text(cached), end="")
                print(f"cadence guard: reused fresh artifact at {SOCIAL_JSON_PATH}")
            return 0

    artifact = build_radar(args)
    if args.json:
        print(json.dumps(artifact, indent=2))
    else:
        print(_render_text(artifact), end="")
        paths = artifact.get("artifacts") or {}
        print(f"saved json: {paths.get('json')}")
        print(f"saved raw: {paths.get('raw')}")
        print(f"saved text: {paths.get('text')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
