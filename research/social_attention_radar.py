#!/usr/bin/env python3
"""
research/social_attention_radar.py — Social Attention Radar V0 (Phase 1G.15).

RESEARCH-ONLY / CACHE-FIRST.  A SEPARATE module from the News Catalyst Radar
(``research/social_arb_radar.py``).  Where that engine confirms news + tape,
this one tries to detect *early crowd attention* — attention-velocity anomalies,
theme diffusion, and crowd-stage — and to separate social-led moves from
news-led ones.

This module emits NO paper signals, NO trade proposals, touches NO execution /
governance / strategy-gate / live-capital / production-universe logic, mutates
NO DB rows.  Social signal is NEVER trade approval.  See
``docs/research/SOCIAL_ARB_REALITY_CHECK.md`` for why the old radar is not this.

Privacy / ToS guardrails:
  - No private-community scraping, no API-ToS bypass.
  - No personal data / PII stored.  Author identities are one-way hashed
    (``_author_hash``) and never persisted raw.
  - Live sources are opt-in and degrade gracefully when unavailable; the
    default automated run uses Google Trends + an operator-curated manual
    JSONL feed only.

Sources (Task 2):
  1. Google Trends — may now generate a STANDALONE research lead (labelled
     SOCIAL_ATTENTION_LEAD), never a trade signal.
  2. StockTwits — public API, OPT-IN (--enable-stocktwits), graceful skip.
  3. Reddit — official API only, OPT-IN and requires creds, graceful skip.
  4. Manual JSONL — data/research/manual_social_items.jsonl (operator-curated).

Outputs:
  cache/research/social_attention_radar_latest.json
  logs/social_attention_radar_latest.txt
  data/research/social_attention_history.jsonl   (append-only, idempotent/day)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean, pstdev
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.scanner_truth import dataio  # cache-only price + jsonl helpers

VERSION = "SOCIAL_ATTENTION_RADAR_V0"

# ── paths ───────────────────────────────────────────────────────────────────────
OUT_JSON = dataio.RESEARCH_CACHE / "social_attention_radar_latest.json"
OUT_TXT = dataio.LOGS_DIR / "social_attention_radar_latest.txt"
HISTORY = dataio.HISTORY_DIR / "social_attention_history.jsonl"
MANUAL_FEED = dataio.HISTORY_DIR / "manual_social_items.jsonl"
NEWS_CATALYST_RAW = dataio.RESEARCH_CACHE / "social_arb_raw_latest.json"
NEWS_CATALYST_LATEST = dataio.RESEARCH_CACHE / "social_arb_latest.json"
OPTIONS_REGIME = dataio.RESEARCH_CACHE / "options_regime_lens_latest.json"
DOCS = dataio.REPO / "docs" / "research"
OUT_MD = DOCS / "SOCIAL_ATTENTION_RADAR_V0.md"

# Phase 1G.15B — cadence / source-health / watch-universe artifacts.
SOURCE_AUDIT_JSON = dataio.RESEARCH_CACHE / "social_attention_source_audit_latest.json"
SOURCE_AUDIT_TXT = dataio.LOGS_DIR / "social_attention_source_audit_latest.txt"
WATCH_JSON = dataio.RESEARCH_CACHE / "social_attention_watch_universe_latest.json"
WATCH_TXT = dataio.LOGS_DIR / "social_attention_watch_universe_latest.txt"
CADENCE_JSON = dataio.RESEARCH_CACHE / "social_attention_cadence_plan_latest.json"

# Cache artifacts the capped watch universe is composed from (all cache-only).
RECALL_SHADOW_LANE = dataio.RESEARCH_CACHE / "recall_repair_shadow_lane_latest.json"
RS_THEME_TRIAGE = dataio.RESEARCH_CACHE / "rs_theme_lens_triage_latest.json"
ALPHA_BOARD = dataio.RESEARCH_CACHE / "alpha_discovery_board_latest.json"

# Watch-universe sizing (Task 3).
WATCH_CAP_DEFAULT = 75
WATCH_CAP_HARD_MAX = 100

# Mega-cap floor so the most-discussed names are always covered.
MEGA_CAP = (
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "AVGO", "AMD",
    "LLY", "JPM", "NFLX", "COST", "ORCL", "PLTR", "COIN", "MSTR",
)

# Source-health labels (Task 2).
HEALTH_HEALTHY = "HEALTHY"
HEALTH_DEGRADED = "DEGRADED"
HEALTH_RATE_LIMITED = "RATE_LIMITED"
HEALTH_DISABLED = "DISABLED"
HEALTH_NOT_CONFIGURED = "NOT_CONFIGURED"
HEALTH_NO_DATA = "NO_DATA"

# Profiles (Task 2).  safe-nightly is the Reddit-free, StockTwits-first profile.
PROFILES = ("default", "safe-nightly", "dry-run")

DISCLAIMER = (
    "RESEARCH-ONLY early-crowd-attention radar.  Labels are research routing only "
    "— NOT buy/sell/trade signals, NOT paper signals, NOT trade proposals.  Social "
    "signal is never trade approval.  No execution / governance / gate / live-capital "
    "/ production-universe changes; no DB writes; no PII stored (authors hashed)."
)

# ── entity / theme tables (self-contained; improved over alias-only) ──────────────
# Cashtags are the highest-confidence social mention form.
CASHTAG_RE = re.compile(r"\$([A-Za-z]{1,5})\b")
BARE_TICKER_RE = re.compile(r"\b([A-Z]{1,5})\b")

# Curated liquid universe used to validate bare-ticker / cashtag tokens.  Kept
# deliberately small and well-known — anything outside it that is not a cashtag
# is treated cautiously.
KNOWN_TICKERS = {
    "AAPL", "MSFT", "NVDA", "GOOGL", "GOOG", "AMZN", "META", "TSLA", "AMD", "AVGO",
    "SMCI", "VRT", "ORCL", "CRM", "NOW", "SNOW", "PLTR", "CRWD", "PANW", "NET",
    "DDOG", "SHOP", "TTD", "NFLX", "SPOT", "DIS", "ROKU", "COIN", "MSTR", "MARA",
    "RIOT", "HOOD", "SOFI", "JPM", "BAC", "GS", "MS", "V", "MA", "PYPL", "XOM",
    "CVX", "OXY", "CCJ", "CEG", "VST", "TLN", "OKLO", "SMR", "NNE", "GEV", "LMT",
    "RTX", "NOC", "BA", "KTOS", "RCAT", "AXON", "LLY", "NVO", "UNH", "HIMS", "F",
    "GM", "RIVN", "LCID", "UBER", "DASH", "ABNB", "RDDT", "SNAP", "TGT", "WMT",
    "COST", "HD", "CAVA", "MP", "RKLB", "LUNR", "ASTS", "ACHR", "JOBY",
}

# alias → ticker.  AMBIGUOUS aliases (generic English words / renamed brands)
# require a company-context word nearby or are suppressed.
COMPANY_ALIASES: Dict[str, str] = {
    "apple": "AAPL", "microsoft": "MSFT", "nvidia": "NVDA", "alphabet": "GOOGL",
    "google": "GOOGL", "amazon": "AMZN", "facebook": "META", "instagram": "META",
    "tesla": "TSLA", "advanced micro devices": "AMD", "broadcom": "AVGO",
    "super micro": "SMCI", "supermicro": "SMCI", "vertiv": "VRT", "oracle": "ORCL",
    "palantir": "PLTR", "crowdstrike": "CRWD", "cloudflare": "NET", "shopify": "SHOP",
    "netflix": "NFLX", "spotify": "SPOT", "disney": "DIS", "coinbase": "COIN",
    "microstrategy": "MSTR", "robinhood": "HOOD", "ford": "F", "rivian": "RIVN",
    "lucid": "LCID", "uber": "UBER", "doordash": "DASH", "airbnb": "ABNB",
    "reddit": "RDDT", "lockheed": "LMT", "raytheon": "RTX", "northrop": "NOC",
    "boeing": "BA", "cameco": "CCJ", "oklo": "OKLO", "nuscale": "SMR",
    "rocket lab": "RKLB", "intuitive machines": "LUNR", "eli lilly": "LLY",
    "novo nordisk": "NVO",
    # ── ambiguous (require context) ──
    "target": "TGT", "snap": "SNAP", "strategy": "MSTR", "meta": "META",
}

# aliases whose bare English usage swamps the company signal — require a
# company-context token nearby or be suppressed.
AMBIGUOUS_ALIASES = frozenset({"apple", "target", "snap", "strategy", "meta", "block"})

# bare single-letter / common-word tickers that must NOT map from a bare token
# (only via cashtag).  "F" (Ford) is the canonical case.
AMBIGUOUS_BARE_TICKERS = frozenset({"F", "A", "T", "V", "MA", "GM", "ALL", "ON", "SO"})

# STRONG company-context tokens used to rescue an AMBIGUOUS alias (generic
# English word / renamed brand) from suppression.  Deliberately narrow — only
# unambiguous market/company words count, so "long-term strategy" or "price
# target" never resolve to a ticker.  Generic momentum words (buy/sell/long/
# short/rally/launch/chip) are intentionally excluded here.
STRONG_CONTEXT_WORDS = frozenset({
    "stock", "stocks", "shares", "share", "ticker", "earnings", "guidance",
    "calls", "puts", "nasdaq", "nyse", "ipo", "dividend", "buyback",
    "market cap", "store", "stores", "retailer", "recall", "recalls",
})

# theme keyword → theme name.
THEME_RULES: Dict[str, Tuple[str, ...]] = {
    "ai_datacenter": ("artificial intelligence", " ai ", "ai chip", "gpu",
                      "data center", "datacenter", "accelerator", "inference"),
    "semiconductors": ("semiconductor", "chip", "foundry", "hbm", "wafer", "memory chip"),
    "power_nuclear": ("nuclear", "uranium", "small modular reactor", "smr",
                      "power demand", "grid", "electricity"),
    "crypto": ("bitcoin", "crypto", "stablecoin", "ethereum", "digital asset"),
    "defense": ("defense", "missile", "drone", "aerospace", "pentagon", "dod contract"),
    "space": ("space", "satellite", "launch", "rocket", "lunar", "orbital"),
    "obesity_healthcare": ("glp-1", "obesity", "weight loss", "weight-loss", "drug trial"),
    "cybersecurity": ("cybersecurity", "breach", "ransomware", "zero trust"),
    "ev": ("electric vehicle", " ev ", "ev demand", "battery", "autonomous"),
}

# theme → likely beneficiary tickers (sympathy mapping).  Research-only impact
# graph; deliberately small.  A theme with no explicit ticker maps to these.
THEME_IMPACT: Dict[str, Tuple[str, ...]] = {
    "ai_datacenter": ("NVDA", "AMD", "AVGO", "SMCI", "VRT"),
    "semiconductors": ("NVDA", "AMD", "AVGO", "SMCI"),
    "power_nuclear": ("CCJ", "CEG", "VST", "OKLO", "SMR", "NNE"),
    "crypto": ("COIN", "MSTR", "MARA", "RIOT", "HOOD"),
    "defense": ("LMT", "RTX", "NOC", "KTOS", "RCAT"),
    "space": ("RKLB", "LUNR", "ASTS", "ACHR", "JOBY"),
    "obesity_healthcare": ("LLY", "NVO", "HIMS"),
    "cybersecurity": ("CRWD", "PANW", "NET"),
    "ev": ("TSLA", "RIVN", "LCID"),
}

MEME_TERMS = frozenset({
    "to the moon", "moon", "yolo", "diamond hands", "tendies", "squeeze",
    "short squeeze", "ape", "apes", "rocket", "🚀", "bagholder", "fomo",
    "pump", "lambo", "stonks",
})

# ── crowd-stage thresholds (documented; NOT tuned to flatter past winners) ────────
STEALTH_MAX_MENTIONS = 15        # total 24h mentions still "under the radar"
RISING_ACCEL = 1.3               # recent hourly rate ≥ 1.3× earlier rate = rising
HIGH_ACCEL = 2.0                 # ≥ 2× = sharp acceleration
VIRAL_MIN_MENTIONS = 60          # 24h mentions that count as "crowd"
PARABOLIC_5D = 0.40              # +40% over 5d = parabolic
BIG_MOVE_5D = 0.12               # +12% over 5d = "price moving"
SOURCE_DIVERSITY_BROAD = 2       # distinct source_types for "multiple communities"

# news-separation window: |social_first_seen - news_first_seen| ≤ this ⇒ SIMULTANEOUS
SIMULTANEOUS_WINDOW_HOURS = 6.0


# ── social item schema (Task 3) ──────────────────────────────────────────────────
@dataclass
class SocialItem:
    source: str
    source_type: str
    timestamp: str            # ISO-8601 UTC
    text: str
    url: str
    ticker_candidates: List[str]
    theme_candidates: List[str]
    author_hash: Optional[str]      # one-way hash, never raw PII
    source_id: Optional[str]
    engagement: Optional[int]
    comments: Optional[int]
    reposts: Optional[int]
    confidence: float
    mapping_method: str             # explicit_ticker|cashtag|company_alias_context|
                                    # theme_only|sympathy_mapping|ambiguous_suppressed
    raw_source_ref: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ── small utils ──────────────────────────────────────────────────────────────────
def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw[:19], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _age_hours(dt: Optional[datetime], now: Optional[datetime] = None) -> float:
    if dt is None:
        return 9999.0
    return max(0.0, ((now or _utc_now()) - dt).total_seconds() / 3600.0)


def _author_hash(author: Any) -> Optional[str]:
    """One-way hash of an author handle.  Never store raw identities (PII)."""
    s = str(author or "").strip().lower()
    if not s:
        return None
    return "a_" + hashlib.sha256(s.encode("utf-8")).hexdigest()[:12]


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _i(v: Any) -> Optional[int]:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


# ── entity / theme mapping (Task 4) ───────────────────────────────────────────────
def detect_themes(text: str) -> List[str]:
    low = f" {text.lower()} "
    out: List[str] = []
    for theme, terms in THEME_RULES.items():
        if any(term in low for term in terms):
            out.append(theme)
    return out


def _has_context(text_low: str) -> bool:
    """True if a STRONG company-context word is present — used to rescue an
    ambiguous alias from suppression.  ``price target`` is explicitly NOT a
    company context (it is analyst jargon), so a bare ``target`` mention next to
    it stays suppressed."""
    if "price target" in text_low:
        return False
    return any(w in text_low for w in STRONG_CONTEXT_WORDS)


def map_text_to_tickers(text: str) -> Tuple[List[str], str]:
    """Map a single social text to ticker candidates + the mapping method.

    Confidence ordering (best → worst):
      cashtag > explicit_ticker > company_alias_context > sympathy_mapping >
      theme_only ; ambiguous hits with no context → ambiguous_suppressed.

    Returns (tickers, method).  ``method`` is the strongest method that produced
    a ticker (or ``theme_only`` / ``ambiguous_suppressed`` when none did).
    """
    if not text:
        return [], "theme_only"
    low = text.lower()
    has_ctx = _has_context(low)

    # 1) cashtags — highest confidence, unambiguous by construction.
    cashtags = {m.group(1).upper() for m in CASHTAG_RE.finditer(text)}
    if cashtags:
        return sorted(cashtags), "cashtag"

    # 2) explicit bare ticker tokens (uppercase) that are known symbols and not
    #    ambiguous single words.
    explicit: set[str] = set()
    for m in BARE_TICKER_RE.finditer(text):
        tok = m.group(1)
        if tok in KNOWN_TICKERS and tok not in AMBIGUOUS_BARE_TICKERS:
            explicit.add(tok)
    if explicit:
        return sorted(explicit), "explicit_ticker"

    # 3) company aliases.  Ambiguous aliases need a context word; otherwise the
    #    hit is suppressed (recorded as ambiguous_suppressed so callers can see
    #    *why* nothing mapped).
    alias_hits: set[str] = set()
    ambiguous_seen = False
    for alias, ticker in COMPANY_ALIASES.items():
        if alias not in low:
            continue
        if alias in AMBIGUOUS_ALIASES:
            ambiguous_seen = True
            if not has_ctx:
                continue  # suppressed: generic word, no company context
        alias_hits.add(ticker)
    if alias_hits:
        return sorted(alias_hits), "company_alias_context"

    # 4) theme-only / sympathy mapping.
    themes = detect_themes(text)
    if themes:
        beneficiaries: set[str] = set()
        for th in themes:
            beneficiaries.update(THEME_IMPACT.get(th, ()))
        if beneficiaries:
            return sorted(beneficiaries), "sympathy_mapping"
        return [], "theme_only"

    if ambiguous_seen:
        return [], "ambiguous_suppressed"
    return [], "theme_only"


_METHOD_CONFIDENCE = {
    "cashtag": 0.95,
    "explicit_ticker": 0.85,
    "company_alias_context": 0.65,
    "sympathy_mapping": 0.35,
    "theme_only": 0.20,
    "ambiguous_suppressed": 0.0,
}


# ── source collection ─────────────────────────────────────────────────────────────
def _new_stats() -> Dict[str, Any]:
    return {"source_status": {}, "source_errors": [], "api_attempts": {}}


def _raw_item(source: str, source_type: str, timestamp: str, text: str,
              url: str = "", author: Any = None, source_id: Any = None,
              engagement: Any = None, comments: Any = None, reposts: Any = None,
              ref: str = "") -> Dict[str, Any]:
    return {
        "source": source, "source_type": source_type, "timestamp": timestamp,
        "text": text, "url": url, "author": author, "source_id": source_id,
        "engagement": engagement, "comments": comments, "reposts": reposts,
        "raw_source_ref": ref,
    }


def collect_google_trends(stats: Dict[str, Any], z_threshold: float = 1.5,
                          limit: int = 40) -> List[Dict[str, Any]]:
    """Google Trends spikes as STANDALONE social-attention rows (Task 2.1).

    Reuses the hardened fetcher in the News Catalyst Radar (rate-limit aware,
    urllib3-shimmed, never raises).  Lazy import keeps this module light and
    provider-free at import time."""
    try:
        from research.social_arb_radar import (COMPANY_ALIASES as _A,
                                               FALLBACK_SYMBOLS as _F,
                                               _fetch_google_trends)
    except Exception as exc:  # pragma: no cover - optional dependency path
        stats["source_errors"].append(f"google_trends import failed: {type(exc).__name__}: {exc}")
        stats["source_status"]["google_trends"] = "import failed"
        return []
    universe = [s for s in _A.keys() if s in _F]
    for s in sorted(_A.keys()):
        if s not in universe:
            universe.append(s)
    rows = _fetch_google_trends(universe[:limit], stats, z_threshold=z_threshold)
    out: List[Dict[str, Any]] = []
    for r in rows:
        z = (r.get("raw") or {}).get("z_vs_7d_baseline")
        out.append(_raw_item(
            source="Google Trends", source_type="google_trends",
            timestamp=r.get("timestamp") or _utc_now().isoformat(),
            text=r.get("title") or "", url=r.get("url") or "",
            source_id=r.get("symbol"), engagement=None,
            ref=f"trends:{r.get('symbol')}",
        ) | {"_explicit_ticker": str(r.get("symbol") or "").upper(), "_trend_z": z})
    stats["source_status"].setdefault("google_trends", f"{len(out)} spike(s)")
    return out


def collect_manual_feed(stats: Dict[str, Any], path: Path = MANUAL_FEED) -> List[Dict[str, Any]]:
    """Operator-curated social/community observations (Task 2.4).  Each JSONL row
    is a free-form dict; we accept flexible key names and never require PII."""
    rows = dataio.read_jsonl(path)
    out: List[Dict[str, Any]] = []
    for r in rows:
        text = str(r.get("text") or r.get("title") or r.get("body") or "").strip()
        if not text and not (r.get("tickers") or r.get("ticker")):
            continue
        tickers = r.get("tickers") or ([r["ticker"]] if r.get("ticker") else [])
        out.append(_raw_item(
            source=str(r.get("source") or "manual"),
            source_type=str(r.get("source_type") or "manual"),
            timestamp=str(r.get("timestamp") or r.get("ts") or _utc_now().isoformat()),
            text=text, url=str(r.get("url") or ""),
            author=r.get("author"), source_id=r.get("source_id") or r.get("id"),
            engagement=r.get("engagement") or r.get("likes"),
            comments=r.get("comments") or r.get("replies"),
            reposts=r.get("reposts") or r.get("shares"),
            ref="manual_jsonl",
        ) | ({"_explicit_tickers": [str(t).upper() for t in tickers]} if tickers else {})
        )
    stats["source_status"]["manual_feed"] = f"{len(out)} item(s)"
    return out


def collect_stocktwits(stats: Dict[str, Any], symbols: Sequence[str],
                       enabled: bool, limit: int = 30) -> List[Dict[str, Any]]:
    """StockTwits public API (Task 2.2).  OPT-IN only.  Cache-first / graceful:
    never raises, logs a single status line, returns [] when disabled or on any
    error.  Uses only the documented public streams endpoint; no auth, no
    private data, author handles are hashed downstream."""
    if not enabled:
        stats["source_status"]["stocktwits"] = "disabled (use --enable-stocktwits)"
        return []
    try:
        import requests  # local import: provider path only
    except Exception as exc:  # pragma: no cover
        stats["source_errors"].append(f"stocktwits: requests unavailable: {exc}")
        return []
    out: List[Dict[str, Any]] = []
    for sym in list(symbols)[:limit]:
        try:
            resp = requests.get(
                f"https://api.stocktwits.com/api/2/streams/symbol/{sym}.json",
                timeout=10, headers={"User-Agent": "gem-trader-research/0"},
            )
            stats["api_attempts"]["stocktwits"] = stats["api_attempts"].get("stocktwits", 0) + 1
            if resp.status_code != 200:
                continue
            for msg in (resp.json().get("messages") or []):
                ent = msg.get("entities") or {}
                sentiment = ((ent.get("sentiment") or {}) or {}).get("basic")
                out.append(_raw_item(
                    source="StockTwits", source_type="stocktwits",
                    timestamp=str(msg.get("created_at") or ""),
                    text=str(msg.get("body") or ""),
                    url=f"https://stocktwits.com/symbol/{sym}",
                    author=(msg.get("user") or {}).get("username"),
                    source_id=msg.get("id"),
                    engagement=(msg.get("likes") or {}).get("total"),
                    ref=f"stocktwits:{sym}:{sentiment or 'na'}",
                ) | {"_explicit_tickers": [sym.upper()]})
        except Exception as exc:
            stats["source_errors"].append(f"stocktwits {sym}: {type(exc).__name__}")
            continue
    stats["source_status"]["stocktwits"] = f"{len(out)} message(s)"
    return out


def collect_reddit(stats: Dict[str, Any], enabled: bool) -> List[Dict[str, Any]]:
    """Reddit official API (Task 2.3).  OPT-IN and requires REDDIT_CLIENT_ID /
    REDDIT_CLIENT_SECRET.  When creds/PRAW are absent we skip gracefully — we do
    NOT scrape public HTML/JSON endpoints (ToS).  Operators wanting Reddit data
    today should curate it into the manual JSONL feed instead."""
    if not enabled:
        stats["source_status"]["reddit"] = "disabled (use --enable-reddit)"
        return []
    cid = os.getenv("REDDIT_CLIENT_ID", "").strip()
    csec = os.getenv("REDDIT_CLIENT_SECRET", "").strip()
    if not cid or not csec:
        stats["source_status"]["reddit"] = "skipped: no REDDIT_CLIENT_ID/SECRET (use manual feed)"
        return []
    try:
        import praw  # type: ignore
    except Exception:
        stats["source_status"]["reddit"] = "skipped: praw not installed"
        return []
    out: List[Dict[str, Any]] = []
    try:
        reddit = praw.Reddit(client_id=cid, client_secret=csec,
                             user_agent="gem-trader-research/0")
        for sub in ("stocks", "investing", "wallstreetbets"):
            for post in reddit.subreddit(sub).hot(limit=25):
                out.append(_raw_item(
                    source=f"reddit/r/{sub}", source_type="reddit",
                    timestamp=datetime.fromtimestamp(
                        getattr(post, "created_utc", 0), tz=timezone.utc).isoformat(),
                    text=f"{getattr(post, 'title', '')} {getattr(post, 'selftext', '')}".strip(),
                    url=f"https://reddit.com{getattr(post, 'permalink', '')}",
                    author=str(getattr(post, "author", "") or ""),
                    source_id=getattr(post, "id", None),
                    engagement=getattr(post, "score", None),
                    comments=getattr(post, "num_comments", None),
                    ref=f"reddit:{sub}",
                ))
        stats["api_attempts"]["reddit"] = stats["api_attempts"].get("reddit", 0) + 1
    except Exception as exc:
        stats["source_errors"].append(f"reddit fetch failed: {type(exc).__name__}: {exc}")
    stats["source_status"]["reddit"] = f"{len(out)} post(s)"
    return out


def _offline_sample() -> List[Dict[str, Any]]:
    """Deterministic offline fixtures for smoke/compile/test runs."""
    now = _utc_now()
    mk = lambda h: (now - timedelta(hours=h)).isoformat()
    return [
        _raw_item("StockTwits", "stocktwits", mk(0.5), "$RKLB looking ready, volume building",
                  author="user_a", source_id="1", engagement=12, ref="s1") | {"_explicit_tickers": ["RKLB"]},
        _raw_item("StockTwits", "stocktwits", mk(1.0), "$RKLB space launch cadence insane 🚀",
                  author="user_b", source_id="2", engagement=40, ref="s2") | {"_explicit_tickers": ["RKLB"]},
        _raw_item("manual", "discord_note", mk(1.5), "RKLB chatter rising in a few rooms, no news yet",
                  author="user_c", source_id="3", engagement=5, ref="m1") | {"_explicit_tickers": ["RKLB"]},
        _raw_item("manual", "manual", mk(2.0), "Nvidia AI chip demand still the talk of the tape",
                  author="user_d", source_id="4", engagement=200, ref="m2"),
        _raw_item("StockTwits", "stocktwits", mk(0.2), "$NVDA to the moon, calls printing, squeeze incoming",
                  author="user_e", source_id="5", engagement=900, comments=300, ref="s3") | {"_explicit_tickers": ["NVDA"]},
        _raw_item("Google Trends", "google_trends", mk(0.1),
                  "Relative Google Trends spike for $RKLB", source_id="RKLB", ref="t1")
        | {"_explicit_ticker": "RKLB", "_trend_z": 2.4},
    ]


def collect_raw(args: argparse.Namespace, stats: Dict[str, Any],
                watch_universe: Optional[Sequence[str]] = None) -> List[Dict[str, Any]]:
    if args.offline_sample:
        stats["source_status"]["offline_sample"] = "used"
        return _offline_sample()
    raw: List[Dict[str, Any]] = []
    if not getattr(args, "skip_manual", False):
        raw.extend(collect_manual_feed(stats))
    if not getattr(args, "skip_google_trends", False):
        raw.extend(collect_google_trends(
            stats, z_threshold=getattr(args, "google_trends_z", 1.5),
            limit=int(getattr(args, "google_trends_cap", 40))))
    # StockTwits symbols: in safe-nightly the capped watch universe drives the
    # public-API calls so we never blast the whole market.  Otherwise prefer the
    # names that already have manual/trends attention.
    if watch_universe:
        st_syms = list(watch_universe)
    else:
        st_syms = sorted({t for r in raw for t in _row_explicit_tickers(r)}) or sorted(KNOWN_TICKERS)
    st_limit = int(getattr(args, "stocktwits_cap", WATCH_CAP_HARD_MAX))
    raw.extend(collect_stocktwits(stats, st_syms,
                                  enabled=getattr(args, "enable_stocktwits", False),
                                  limit=st_limit))
    raw.extend(collect_reddit(stats, enabled=getattr(args, "enable_reddit", False)))
    return raw


def _row_explicit_tickers(r: Dict[str, Any]) -> List[str]:
    out = list(r.get("_explicit_tickers") or [])
    one = r.get("_explicit_ticker")
    if one:
        out.append(str(one).upper())
    return [t for t in {x.upper() for x in out} if t]


# ── capped watch universe (Task 3) ────────────────────────────────────────────────
def _manual_watchlist_tickers() -> List[str]:
    """Tickers an operator put in the manual feed — they are watch-worthy by
    construction.  Cache-only read of the JSONL feed; [] when absent."""
    out: List[str] = []
    for r in dataio.read_jsonl(MANUAL_FEED):
        for t in (r.get("tickers") or ([r["ticker"]] if r.get("ticker") else [])):
            if t:
                out.append(str(t).upper())
    return out


def _lens_ready_tickers(cap: int = 40) -> List[str]:
    """Tickers whose cached Stock Lens reads LENS_READY-ish.  Lightweight glob;
    reads only the published label/state fields.  Cache-only."""
    out: List[str] = []
    try:
        for p in sorted(dataio.RESEARCH_CACHE.glob("stock_lens_*_latest.json"))[: cap * 2]:
            try:
                d = json.loads(p.read_text())
            except Exception:
                continue
            label = f"{d.get('label', '')} {d.get('state', '')} {d.get('confidence', '')}".upper()
            if "READY" in label:
                sym = p.stem.replace("stock_lens_", "").replace("_latest", "").upper()
                if sym:
                    out.append(sym)
            if len(out) >= cap:
                break
    except Exception:
        pass
    return out


def build_watch_universe(cap: int = WATCH_CAP_DEFAULT,
                         hard_max: int = WATCH_CAP_HARD_MAX) -> Dict[str, Any]:
    """Compose a capped social watch universe so the radar never scans the whole
    market.  Priority order (Task 3): recall-shadow top 50 → RS/theme triage
    top 30 → Alpha board → Lens LENS_READY → manual watchlist → mega-cap.
    Dedup preserves first (highest-priority) occurrence.  Cache-only."""
    cap = max(1, min(int(cap), int(hard_max)))
    sources: Dict[str, List[str]] = {}

    def _from(path: Path, key: str, top: Optional[int] = None) -> List[str]:
        data = _load_json_safe(path)
        rows = (data.get("board") or data.get("candidates") or data.get("items")
                or data.get("leads") or []) if isinstance(data, dict) else []
        syms: List[str] = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            s = str(r.get("ticker") or r.get("symbol") or "").upper()
            if s:
                syms.append(s)
        if top:
            syms = syms[:top]
        sources[key] = syms
        return syms

    ordered: List[str] = []
    buckets = [
        _from(RECALL_SHADOW_LANE, "recall_shadow_top50", 50),
        _from(RS_THEME_TRIAGE, "rs_theme_triage_top30", 30),
        _from(ALPHA_BOARD, "alpha_board"),
    ]
    lens = _lens_ready_tickers()
    sources["lens_ready"] = lens
    manual = _manual_watchlist_tickers()
    sources["manual_watchlist"] = manual
    sources["mega_cap"] = list(MEGA_CAP)
    buckets += [lens, manual, list(MEGA_CAP)]

    seen: set[str] = set()
    for bucket in buckets:
        for s in bucket:
            if s and s not in seen and s not in COMMON_FALSE_TICKERS:
                seen.add(s)
                ordered.append(s)
            if len(ordered) >= cap:
                break
        if len(ordered) >= cap:
            break

    return {
        "kind": "social_attention_watch_universe",
        "version": VERSION,
        "generated_at": _utc_now().isoformat(),
        "cap": cap,
        "hard_max": hard_max,
        "size": len(ordered),
        "universe": ordered,
        "source_counts": {k: len(v) for k, v in sources.items()},
    }


# generic words that should never enter the watch universe as tickers.
COMMON_FALSE_TICKERS = frozenset({"A", "I", "ALL", "ON", "OR", "SO", "IT", "BE", "AT"})


def _load_json_safe(path: Path) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text())
    except Exception:
        pass
    return {}


# ── source health (Task 2) ─────────────────────────────────────────────────────────
def compute_source_health(stats: Dict[str, Any], raw: Sequence[Dict[str, Any]],
                          enabled: Dict[str, bool]) -> Dict[str, Dict[str, Any]]:
    """Classify every source into one of HEALTHY / DEGRADED / RATE_LIMITED /
    DISABLED / NOT_CONFIGURED / NO_DATA.  Reddit is intentionally DISABLED /
    NOT_CONFIGURED here and must never block the run."""
    counts: Dict[str, int] = {}
    for r in raw:
        st = str(r.get("source_type") or "")
        counts[st] = counts.get(st, 0) + 1
    errors = " ".join(stats.get("source_errors") or []).lower()
    status = stats.get("source_status") or {}

    def mk(label, n, detail):
        return {"health": label, "items": n, "detail": detail}

    health: Dict[str, Dict[str, Any]] = {}

    # ── manual ──
    n_manual = counts.get("manual", 0) + sum(
        c for st, c in counts.items() if st not in ("google_trends", "stocktwits", "reddit"))
    if not enabled.get("manual", True):
        health["manual"] = mk(HEALTH_DISABLED, 0, "skipped by flag")
    elif not MANUAL_FEED.exists():
        health["manual"] = mk(HEALTH_NOT_CONFIGURED, 0,
                              f"no {dataio.rel_to_repo(MANUAL_FEED)} (see *.example.jsonl)")
    elif n_manual > 0:
        health["manual"] = mk(HEALTH_HEALTHY, n_manual, "operator-curated items present")
    else:
        health["manual"] = mk(HEALTH_NO_DATA, 0, "feed present but empty")

    # ── stocktwits ──
    n_st = counts.get("stocktwits", 0)
    if not enabled.get("stocktwits", False):
        health["stocktwits"] = mk(HEALTH_DISABLED, 0, "opt-in (enable via profile/flag)")
    elif "stocktwits" in errors and ("429" in errors or "rate" in errors):
        health["stocktwits"] = mk(HEALTH_RATE_LIMITED, n_st, "public API rate-limited")
    elif "requests unavailable" in errors:
        health["stocktwits"] = mk(HEALTH_NOT_CONFIGURED, 0, "requests lib unavailable")
    elif n_st > 0 and "stocktwits" in errors:
        health["stocktwits"] = mk(HEALTH_DEGRADED, n_st, "partial: some symbols errored")
    elif n_st > 0:
        health["stocktwits"] = mk(HEALTH_HEALTHY, n_st, "messages collected")
    else:
        health["stocktwits"] = mk(HEALTH_NO_DATA, 0, status.get("stocktwits", "no messages"))

    # ── google_trends ──
    n_gt = counts.get("google_trends", 0)
    gt_status = str(status.get("google_trends", "")).lower()
    if not enabled.get("google_trends", True):
        health["google_trends"] = mk(HEALTH_DISABLED, 0, "skipped by flag")
    elif "rate-limited" in errors or ("429" in errors and "google_trends" in errors):
        health["google_trends"] = mk(HEALTH_RATE_LIMITED, n_gt, "pytrends 429 (auxiliary; expected)")
    elif "missing" in gt_status or "import failed" in gt_status or "init failed" in gt_status:
        health["google_trends"] = mk(HEALTH_NOT_CONFIGURED, 0, "pytrends unavailable")
    elif n_gt > 0:
        health["google_trends"] = mk(HEALTH_HEALTHY, n_gt, "spikes collected")
    else:
        health["google_trends"] = mk(HEALTH_NO_DATA, 0, "ran, no qualifying spikes")

    # ── reddit (intentionally not on the required path) ──
    n_rd = counts.get("reddit", 0)
    rd_status = str(status.get("reddit", "")).lower()
    if not enabled.get("reddit", False):
        health["reddit"] = mk(HEALTH_DISABLED, 0,
                              "intentionally skipped — not required; official API only")
    elif "no reddit_client" in rd_status or "praw not installed" in rd_status:
        health["reddit"] = mk(HEALTH_NOT_CONFIGURED, 0,
                              "awaiting official REDDIT_CLIENT_ID/SECRET")
    elif n_rd > 0:
        health["reddit"] = mk(HEALTH_HEALTHY, n_rd, "posts collected")
    else:
        health["reddit"] = mk(HEALTH_NO_DATA, 0, rd_status or "no posts")

    return health


# ── profile resolution (Task 2) ───────────────────────────────────────────────────
def apply_profile(args: argparse.Namespace) -> argparse.Namespace:
    """Mutate args in place for the chosen --profile.  safe-nightly is the
    Reddit-free, StockTwits-first profile that accumulates history safely:
    capped watch universe, StockTwits on, low-cap Google Trends, manual feed on,
    Reddit OFF.  ``dry-run`` is safe-nightly + no writes."""
    profile = getattr(args, "profile", "default")
    if profile in ("safe-nightly", "dry-run"):
        args.enable_stocktwits = True
        args.enable_reddit = False           # Reddit never on the required path
        args.skip_manual = False
        args.skip_google_trends = False
        args.use_watch_universe = True
        # low-cap, rate-limit-prone Google Trends
        if getattr(args, "google_trends_cap", None) in (None, 40):
            args.google_trends_cap = 12
        if profile == "dry-run":
            args.dry_run = True
    return args


# ── normalization (Task 3 + 4) ────────────────────────────────────────────────────
def normalize_items(raw_items: Sequence[Dict[str, Any]]) -> List[SocialItem]:
    items: List[SocialItem] = []
    for r in raw_items:
        text = str(r.get("text") or "")
        explicit = _row_explicit_tickers(r)
        if explicit:
            # Source already declares the subject ticker(s) (StockTwits symbol
            # stream, Trends symbol, manual ticker) — treat as explicit.
            tickers, method = sorted(set(explicit)), "explicit_ticker"
            # still detect themes from the text for diffusion analysis
            themes = detect_themes(text)
        else:
            tickers, method = map_text_to_tickers(text)
            themes = detect_themes(text)
        conf = _METHOD_CONFIDENCE.get(method, 0.2)
        items.append(SocialItem(
            source=str(r.get("source") or ""),
            source_type=str(r.get("source_type") or "unknown"),
            timestamp=(_parse_dt(r.get("timestamp")) or _utc_now()).isoformat(),
            text=text[:500],
            url=str(r.get("url") or ""),
            ticker_candidates=tickers,
            theme_candidates=themes,
            author_hash=_author_hash(r.get("author")),
            source_id=(str(r.get("source_id")) if r.get("source_id") is not None else None),
            engagement=_i(r.get("engagement")),
            comments=_i(r.get("comments")),
            reposts=_i(r.get("reposts")),
            confidence=round(conf, 3),
            mapping_method=method,
            raw_source_ref=str(r.get("raw_source_ref") or ""),
        ))
    return items


# ── attention-velocity metrics (Task 5) ───────────────────────────────────────────
def _window_counts(timestamps: List[datetime], now: datetime) -> Dict[str, int]:
    out = {"1h": 0, "6h": 0, "24h": 0, "7d": 0}
    for ts in timestamps:
        age = _age_hours(ts, now)
        if age <= 1:
            out["1h"] += 1
        if age <= 6:
            out["6h"] += 1
        if age <= 24:
            out["24h"] += 1
        if age <= 24 * 7:
            out["7d"] += 1
    return out


def _baseline_from_history(ticker: str, history: List[Dict[str, Any]]) -> Tuple[List[float], Optional[datetime], int]:
    """Return (prior 24h-mention series, earliest first_seen, n_appearances) for
    a ticker from prior runs of this radar.  Cache-only / append-only history."""
    series: List[float] = []
    first_seen: Optional[datetime] = None
    appearances = 0
    for row in history:
        if str(row.get("ticker", "")).upper() != ticker.upper():
            continue
        appearances += 1
        m = row.get("metrics") or {}
        c = m.get("mention_count_24h")
        if isinstance(c, (int, float)):
            series.append(float(c))
        fs = _parse_dt(row.get("first_seen_at"))
        if fs and (first_seen is None or fs < first_seen):
            first_seen = fs
    return series, first_seen, appearances


def compute_velocity(items: List[SocialItem], history: List[Dict[str, Any]],
                     now: Optional[datetime] = None) -> Dict[str, Dict[str, Any]]:
    """Aggregate per-ticker attention velocity metrics + 0-100 scores."""
    now = now or _utc_now()
    by_ticker: Dict[str, List[SocialItem]] = {}
    for it in items:
        for t in it.ticker_candidates:
            by_ticker.setdefault(t, []).append(it)

    out: Dict[str, Dict[str, Any]] = {}
    for ticker, its in by_ticker.items():
        tss = [_parse_dt(it.timestamp) or now for it in its]
        counts = _window_counts(tss, now)
        unique_sources = len({it.source_type for it in its})
        unique_authors = len({it.author_hash for it in its if it.author_hash})
        engagement_24h = sum(it.engagement or 0 for it in its
                             if _age_hours(_parse_dt(it.timestamp), now) <= 24)

        # acceleration: recent 6h hourly rate vs the prior 18h hourly rate.
        recent_rate = counts["6h"] / 6.0
        earlier = max(0, counts["24h"] - counts["6h"])
        earlier_rate = earlier / 18.0
        accel = (recent_rate / earlier_rate) if earlier_rate > 0 else (
            float(HIGH_ACCEL + 1) if recent_rate > 0 else 0.0)

        # z-score vs this radar's own history for the ticker.
        base_series, hist_first_seen, appearances = _baseline_from_history(ticker, history)
        z: Optional[float] = None
        if len(base_series) >= 3:
            mu = mean(base_series)
            sd = pstdev(base_series)
            if sd > 0:
                z = (counts["24h"] - mu) / sd

        first_seen = hist_first_seen or min(tss) if tss else now
        time_since_first = _age_hours(first_seen, now)

        # ── 0-100 scores ──
        accel_component = _clamp(accel, 0, 3) / 3.0 * 60.0
        z_component = (_clamp(z, 0, 4) / 4.0 * 40.0) if z is not None else 0.0
        velocity_score = round(_clamp(accel_component + z_component, 0, 100), 1)

        # novelty: brand-new + low prior footprint scores high; decays with
        # repeated appearances and high absolute mention counts.
        novelty = 100.0 - 18.0 * appearances - _clamp(counts["24h"] - STEALTH_MAX_MENTIONS, 0, 60)
        novelty_score = round(_clamp(novelty, 0, 100), 1)

        diversity_score = round(_clamp(unique_sources * 28 + unique_authors * 6, 0, 100), 1)

        meme_hits = sum(1 for it in its for term in MEME_TERMS if term in it.text.lower())

        out[ticker] = {
            "ticker": ticker,
            "mention_count_1h": counts["1h"],
            "mention_count_6h": counts["6h"],
            "mention_count_24h": counts["24h"],
            "mention_count_7d": counts["7d"],
            "unique_source_count": unique_sources,
            "unique_author_count": unique_authors,
            "engagement_velocity": engagement_24h,
            "mention_z_score": round(z, 3) if z is not None else None,
            "acceleration_ratio": round(accel, 3),
            "source_diversity": unique_sources,
            "first_seen_at": first_seen.isoformat(),
            "time_since_first_seen_hours": round(time_since_first, 2),
            "freshness_hours": round(min((_age_hours(t, now) for t in tss), default=9999.0), 2),
            "novelty_score": novelty_score,
            "history_appearances": appearances,
            "meme_hits": meme_hits,
            "best_mapping_method": _best_method(its),
            "best_confidence": round(max((it.confidence for it in its), default=0.0), 3),
            "attention_velocity_score": velocity_score,
            "attention_novelty_score": novelty_score,
            "source_diversity_score": diversity_score,
            "sample_texts": [it.text[:140] for it in its[:3]],
        }
    return out


def _best_method(items: List[SocialItem]) -> str:
    order = ["cashtag", "explicit_ticker", "company_alias_context",
             "sympathy_mapping", "theme_only", "ambiguous_suppressed"]
    methods = {it.mapping_method for it in items}
    for m in order:
        if m in methods:
            return m
    return "theme_only"


# ── overlays (Task 8) — context, never approval ──────────────────────────────────
def _price_overlay(ticker: str) -> Dict[str, Any]:
    """Cache-only tape/RS overlay.  Best-effort; {} when no parquet."""
    try:
        df = dataio.load_prices(ticker)
        spy = dataio.load_prices("SPY")
        qqq = dataio.load_prices("QQQ")
    except Exception:
        return {}
    if df is None or "close" not in df.columns or len(df) < 25:
        return {}
    close = df["close"].astype(float)

    def ret(s, n):
        if len(s) <= n or s.iloc[-1-n] <= 0:
            return None
        return float(s.iloc[-1] / s.iloc[-1-n] - 1.0)

    r5 = ret(close, 5)
    r20 = ret(close, 20)
    vol = df["volume"].astype(float) if "volume" in df.columns else None
    rel_vol = None
    if vol is not None and len(vol) >= 21 and vol.iloc[-21:-1].mean() > 0:
        rel_vol = float(vol.iloc[-1] / vol.iloc[-21:-1].mean())
    ma50 = float(close.iloc[-50:].mean()) if len(close) >= 50 else None
    extension = (float(close.iloc[-1] / ma50 - 1.0) if ma50 and ma50 > 0 else None)
    rs_spy = None
    if spy is not None and len(spy) > 20:
        sr = ret(spy["close"].astype(float), 20)
        if r20 is not None and sr is not None:
            rs_spy = round(r20 - sr, 4)
    rs_qqq = None
    if qqq is not None and len(qqq) > 20:
        qr = ret(qqq["close"].astype(float), 20)
        if r20 is not None and qr is not None:
            rs_qqq = round(r20 - qr, 4)
    return {
        "available": True,
        "return_5d": round(r5, 4) if r5 is not None else None,
        "return_20d": round(r20, 4) if r20 is not None else None,
        "rel_volume": round(rel_vol, 2) if rel_vol is not None else None,
        "rs_vs_spy_20d": rs_spy,
        "rs_vs_qqq_20d": rs_qqq,
        "extension_above_ma50": round(extension, 4) if extension is not None else None,
        "price_moving": (r5 is not None and abs(r5) >= BIG_MOVE_5D),
        "parabolic": (r5 is not None and r5 >= PARABOLIC_5D),
    }


def _options_overlay(ticker: str) -> Dict[str, Any]:
    if not OPTIONS_REGIME.exists():
        return {}
    try:
        data = json.loads(OPTIONS_REGIME.read_text())
    except Exception:
        return {}
    per = data.get("per_symbol") or data.get("symbols") or {}
    row = per.get(ticker) if isinstance(per, dict) else None
    if not isinstance(row, dict):
        return {}
    return {
        "available": True,
        "options_quality": row.get("quality") or row.get("options_quality"),
        "regime": row.get("regime") or row.get("options_regime"),
        "speculative": bool(row.get("speculative")),
    }


# ── news-led vs social-led separation (Task 7) ────────────────────────────────────
def _news_first_seen() -> Dict[str, datetime]:
    """Earliest news timestamp per ticker from the News Catalyst Radar artifacts.
    Cache-only; {} when artifacts absent."""
    out: Dict[str, datetime] = {}
    for path in (NEWS_CATALYST_RAW, NEWS_CATALYST_LATEST):
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text())
        except Exception:
            continue
        rows = (data if isinstance(data, list) else
                data.get("items") or data.get("raw_items") or data.get("candidates") or [])
        for r in rows if isinstance(rows, list) else []:
            if not isinstance(r, dict):
                continue
            ts = _parse_dt(r.get("timestamp") or r.get("publishedDate") or r.get("date"))
            if ts is None:
                continue
            syms = []
            if r.get("symbol"):
                syms.append(str(r["symbol"]).upper())
            if r.get("ticker"):
                syms.append(str(r["ticker"]).upper())
            for t in r.get("tickers_mentioned") or []:
                syms.append(str(t).upper())
            for t in {s for s in syms if s}:
                if t not in out or ts < out[t]:
                    out[t] = ts
    return out


def classify_lead_type(ticker: str, social_first_seen: datetime,
                       news_map: Dict[str, datetime]) -> Tuple[str, Optional[float]]:
    news_ts = news_map.get(ticker.upper())
    if news_ts is None:
        return "UNKNOWN", None
    delta_h = (news_ts - social_first_seen).total_seconds() / 3600.0
    # delta_h > 0  → news AFTER social ⇒ social-led
    if abs(delta_h) <= SIMULTANEOUS_WINDOW_HOURS:
        return "SIMULTANEOUS", round(delta_h, 2)
    if delta_h > 0:
        return "SOCIAL_LED", round(delta_h, 2)
    return "NEWS_LED", round(delta_h, 2)


# ── crowd-stage classification (Task 6) ───────────────────────────────────────────
def classify_stage(metrics: Dict[str, Any], price: Dict[str, Any],
                   options: Dict[str, Any]) -> str:
    total = metrics.get("mention_count_24h", 0)
    accel = metrics.get("acceleration_ratio", 0.0) or 0.0
    diversity = metrics.get("source_diversity", 0)
    meme = metrics.get("meme_hits", 0) > 0
    parabolic = bool(price.get("parabolic"))
    moving = bool(price.get("price_moving"))
    speculative_opts = bool(options.get("speculative"))

    # Exhaustion / viral checked first when the move is already large.
    if parabolic and (meme or speculative_opts):
        return "EXHAUSTION_RISK"
    if total >= VIRAL_MIN_MENTIONS and meme and moving:
        return "VIRAL_CROWDING"
    if total <= STEALTH_MAX_MENTIONS and accel >= HIGH_ACCEL and diversity <= 1:
        return "STEALTH_ATTENTION"
    if diversity >= SOURCE_DIVERSITY_BROAD and (moving or metrics.get("attention_velocity_score", 0) >= 50):
        return "BROADENING_ATTENTION"
    if accel >= RISING_ACCEL and total < VIRAL_MIN_MENTIONS:
        return "EARLY_DISCOVERY"
    return "NO_SIGNAL"


# ── label assignment (Task 8) ─────────────────────────────────────────────────────
def assign_label(stage: str, lead_type: str, metrics: Dict[str, Any],
                 price: Dict[str, Any]) -> str:
    """Research routing label.  NEVER buy/sell/execute/approved."""
    if stage == "NO_SIGNAL":
        return "NO_SOCIAL_EDGE"
    if stage == "EXHAUSTION_RISK" or stage == "VIRAL_CROWDING":
        return "CROWDED_ATTENTION"
    if metrics.get("best_mapping_method") in ("theme_only", "sympathy_mapping"):
        return "SOCIAL_THEME_LEAD"
    if lead_type == "SOCIAL_LED":
        return "SOCIAL_LED_CANDIDATE"
    if lead_type == "NEWS_LED":
        return "NEWS_LED_CANDIDATE"
    if stage in ("STEALTH_ATTENTION", "EARLY_DISCOVERY"):
        # early + needs price/lens confirmation
        return "NEEDS_LENS" if not price.get("available") else "SOCIAL_ATTENTION_LEAD"
    return "NEEDS_FORWARD_VALIDATION"


# ── build ──────────────────────────────────────────────────────────────────────────
def build(args: argparse.Namespace, watch: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    stats = _new_stats()
    now = _utc_now()
    profile = getattr(args, "profile", "default")

    # Capped watch universe drives StockTwits in safe-nightly so we never scan
    # the whole market.  Skipped for offline fixtures.  Computed by the caller
    # (main) so it can also write the watch artifact; recomputed here only if
    # the caller did not supply one.
    use_watch = getattr(args, "use_watch_universe", False) or profile in ("safe-nightly", "dry-run")
    if watch is None and use_watch and not getattr(args, "offline_sample", False):
        watch = build_watch_universe(cap=int(getattr(args, "watch_cap", WATCH_CAP_DEFAULT)))

    raw = collect_raw(args, stats, watch_universe=(watch or {}).get("universe"))
    items = normalize_items(raw)
    history = dataio.read_jsonl(HISTORY)
    velocity = compute_velocity(items, history, now=now)
    news_map = _news_first_seen()

    leads: List[Dict[str, Any]] = []
    for ticker, m in velocity.items():
        price = _price_overlay(ticker)
        options = _options_overlay(ticker)
        stage = classify_stage(m, price, options)
        first_seen = _parse_dt(m["first_seen_at"]) or now
        lead_type, lead_time = classify_lead_type(ticker, first_seen, news_map)
        label = assign_label(stage, lead_type, m, price)
        leads.append({
            **m,
            "crowd_stage": stage,
            "lead_type": lead_type,
            "lead_time_hours": lead_time,
            "label": label,
            "price_overlay": price,
            "options_overlay": options,
        })

    # rank: social-led + early + high velocity first; crowded/no-edge last.
    stage_rank = {"STEALTH_ATTENTION": 0, "EARLY_DISCOVERY": 1, "BROADENING_ATTENTION": 2,
                  "VIRAL_CROWDING": 3, "EXHAUSTION_RISK": 4, "NO_SIGNAL": 5}
    lead_rank = {"SOCIAL_LED": 0, "SIMULTANEOUS": 1, "UNKNOWN": 2, "NEWS_LED": 3}
    leads.sort(key=lambda x: (
        stage_rank.get(x["crowd_stage"], 9),
        lead_rank.get(x["lead_type"], 9),
        -x["attention_velocity_score"],
    ))

    counts = {
        "leads": len([l for l in leads if l["label"] != "NO_SOCIAL_EDGE"]),
        "social_led": len([l for l in leads if l["lead_type"] == "SOCIAL_LED"]),
        "news_led": len([l for l in leads if l["lead_type"] == "NEWS_LED"]),
        "stealth": len([l for l in leads if l["crowd_stage"] == "STEALTH_ATTENTION"]),
        "early": len([l for l in leads if l["crowd_stage"] == "EARLY_DISCOVERY"]),
        "broadening": len([l for l in leads if l["crowd_stage"] == "BROADENING_ATTENTION"]),
        "crowded": len([l for l in leads if l["crowd_stage"] in ("VIRAL_CROWDING", "EXHAUSTION_RISK")]),
    }

    enabled = {
        "manual": not getattr(args, "skip_manual", False),
        "stocktwits": getattr(args, "enable_stocktwits", False),
        "google_trends": not getattr(args, "skip_google_trends", False),
        "reddit": getattr(args, "enable_reddit", False),
    }
    source_health = compute_source_health(stats, raw, enabled)

    return {
        "kind": "social_attention_radar",
        "version": VERSION,
        "research_only": True,
        "generated_at": now.isoformat(),
        "disclaimer": DISCLAIMER,
        "asof_date": now.date().isoformat(),
        "profile": profile,
        "sources_status": stats["source_status"],
        "source_errors": stats["source_errors"][:20],
        "source_health": source_health,
        "api_attempts": stats["api_attempts"],
        "watch_universe": (
            {"size": watch["size"], "cap": watch["cap"],
             "source_counts": watch["source_counts"]} if watch else None),
        "n_raw_items": len(raw),
        "n_normalized_items": len(items),
        "n_tickers": len(velocity),
        "news_artifact_present": bool(news_map),
        "counts": counts,
        "leads": leads,
    }


def _history_rows(res: Dict[str, Any]) -> List[Dict[str, Any]]:
    asof = res["asof_date"]
    rows = []
    for l in res["leads"]:
        rows.append({
            "asof_date": asof,
            "version": VERSION,
            "ticker": l["ticker"],
            "first_seen_at": l["first_seen_at"],
            "crowd_stage": l["crowd_stage"],
            "lead_type": l["lead_type"],
            "lead_time_hours": l["lead_time_hours"],
            "label": l["label"],
            "metrics": {
                "mention_count_24h": l["mention_count_24h"],
                "attention_velocity_score": l["attention_velocity_score"],
                "attention_novelty_score": l["attention_novelty_score"],
                "source_diversity_score": l["source_diversity_score"],
                "acceleration_ratio": l["acceleration_ratio"],
                "mention_z_score": l["mention_z_score"],
            },
        })
    return rows


def _existing_asof_dates(path: Path) -> set[str]:
    return {str(r.get("asof_date")) for r in dataio.read_jsonl(path)}


# ── render ───────────────────────────────────────────────────────────────────────
def render_txt(res: Dict[str, Any]) -> List[str]:
    c = res["counts"]
    L = [
        f"== SOCIAL ATTENTION RADAR ({res['version']}) — {res['generated_at']} ==",
        res["disclaimer"],
        f"raw={res['n_raw_items']} normalized={res['n_normalized_items']} "
        f"tickers={res['n_tickers']} news_artifact={res['news_artifact_present']}",
        f"leads={c['leads']} social_led={c['social_led']} news_led={c['news_led']} "
        f"stealth={c['stealth']} early={c['early']} broadening={c['broadening']} crowded={c['crowded']}",
        "profile=" + str(res.get("profile", "default")) + (
            f"  watch_universe={res['watch_universe']['size']}/{res['watch_universe']['cap']}"
            if res.get("watch_universe") else ""),
        "source health: " + ", ".join(
            f"{k}={v.get('health')}({v.get('items')})"
            for k, v in (res.get("source_health") or {}).items()),
        "",
        f"{'ticker':<7}{'stage':<22}{'lead':<13}{'label':<26}"
        f"{'vel':>5}{'nov':>5}{'div':>5}{'m24':>5}{'r5d':>8}",
    ]
    for l in res["leads"][:25]:
        po = l.get("price_overlay") or {}
        r5 = po.get("return_5d")
        L.append(
            f"{l['ticker']:<7}{l['crowd_stage']:<22}{l['lead_type']:<13}{l['label']:<26}"
            f"{l['attention_velocity_score']:>5.0f}{l['attention_novelty_score']:>5.0f}"
            f"{l['source_diversity_score']:>5.0f}{l['mention_count_24h']:>5}"
            f"{(r5 * 100 if r5 is not None else 0):>7.1f}%")
    if res["source_errors"]:
        L += ["", "source errors:"] + [f"  - {e}" for e in res["source_errors"][:8]]
    return L


def _write_doc() -> None:
    if OUT_MD.exists():
        return
    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text(
        "# Social Attention Radar V0 (Phase 1G.15)\n\n"
        "Research-only early-crowd-attention radar. Separate from the News Catalyst\n"
        "Radar (`social_arb_radar.py`). See `SOCIAL_ARB_REALITY_CHECK.md`.\n\n"
        "## What it does\n"
        "- Ingests Google Trends (standalone lead), an operator-curated manual JSONL\n"
        "  feed, and (opt-in) StockTwits / Reddit official APIs.\n"
        "- Maps text → tickers with explicit/cashtag/alias-context/theme/sympathy\n"
        "  methods, suppressing ambiguous aliases without context.\n"
        "- Computes attention-velocity metrics (mention windows, z-score vs its own\n"
        "  history, acceleration, source/author diversity, novelty) → 0-100 scores.\n"
        "- Classifies crowd stage: STEALTH / EARLY_DISCOVERY / BROADENING /\n"
        "  VIRAL_CROWDING / EXHAUSTION_RISK / NO_SIGNAL.\n"
        "- Separates SOCIAL_LED vs NEWS_LED vs SIMULTANEOUS vs UNKNOWN by comparing\n"
        "  social first-seen to the News Catalyst Radar artifact timestamps.\n"
        "- Attaches cache-only tape/RS/options overlays as CONTEXT (never approval).\n\n"
        "## Labels (research routing only)\n"
        "SOCIAL_ATTENTION_LEAD · SOCIAL_THEME_LEAD · SOCIAL_LED_CANDIDATE ·\n"
        "NEWS_LED_CANDIDATE · CROWDED_ATTENTION · NO_SOCIAL_EDGE · NEEDS_LENS ·\n"
        "NEEDS_FORWARD_VALIDATION.  Never BUY/SELL/EXECUTE/APPROVED.\n\n"
        "## Outputs\n"
        "- `cache/research/social_attention_radar_latest.json`\n"
        "- `logs/social_attention_radar_latest.txt`\n"
        "- `data/research/social_attention_history.jsonl` (append-only, 1 row/ticker/day)\n\n"
        "## Manual feed\n"
        "`data/research/manual_social_items.jsonl` — one JSON object per line. Accepted\n"
        "keys: text/title/body, ticker/tickers, theme/themes, source, source_type,\n"
        "timestamp, url, author (hashed on ingest), engagement, comments, reposts.\n\n"
        "## Run\n"
        "```bash\n"
        "SNIPER_ENV_PATH=/home/gem/secure/trading.env .venv/bin/python research/social_attention_radar.py\n"
        "GEM_TRADER_SKIP_DOTENV=true .venv/bin/python research/social_attention_radar.py --offline-sample --skip-google-trends\n"
        "```\n\n"
        "## Guardrails\n"
        "Research-only. No paper signals, trade proposals, execution/governance/gate/\n"
        "live-capital/universe changes, no DB writes. Social signal is never trade\n"
        "approval. No private scraping, no PII (authors hashed). Forward edge must be\n"
        "proven by `social_attention_forward_validation.py` before any lens routing.\n"
    )


def _source_audit_artifact(res: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "kind": "social_attention_source_audit",
        "version": VERSION,
        "generated_at": res.get("generated_at"),
        "profile": res.get("profile"),
        "source_health": res.get("source_health"),
        "source_errors": res.get("source_errors"),
        "api_attempts": res.get("api_attempts"),
        "watch_universe": res.get("watch_universe"),
        "reddit_policy": ("DISABLED — Reddit is intentionally not on the required "
                          "path (Phase 1G.15B); official API only, never scraped, "
                          "and must never block history accumulation."),
        "note": "Reddit-free cadence: StockTwits-first, manual JSONL fallback, "
                "Google Trends auxiliary/low-cap.",
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Social Attention Radar V0 (research-only)")
    ap.add_argument("--profile", choices=PROFILES, default="default",
                    help="default | safe-nightly (StockTwits-first, Reddit-free) | dry-run")
    ap.add_argument("--offline-sample", action="store_true", help="use deterministic fixtures")
    ap.add_argument("--skip-google-trends", action="store_true")
    ap.add_argument("--skip-manual", action="store_true")
    ap.add_argument("--enable-stocktwits", action="store_true", help="opt-in StockTwits public API")
    ap.add_argument("--enable-reddit", action="store_true", help="opt-in Reddit (needs official creds)")
    ap.add_argument("--use-watch-universe", action="store_true",
                    help="drive StockTwits from the capped watch universe")
    ap.add_argument("--watch-cap", type=int, default=WATCH_CAP_DEFAULT)
    ap.add_argument("--stocktwits-cap", type=int, default=WATCH_CAP_HARD_MAX)
    ap.add_argument("--google-trends-z", type=float, default=1.5)
    ap.add_argument("--google-trends-cap", type=int, default=40)
    ap.add_argument("--dry-run", action="store_true", help="print, write nothing")
    ap.add_argument("--print", action="store_true")
    args = apply_profile(ap.parse_args(argv))

    # Compute the watch universe once so we can also write its artifact.
    use_watch = getattr(args, "use_watch_universe", False) or args.profile in ("safe-nightly", "dry-run")
    watch = (build_watch_universe(cap=int(args.watch_cap))
             if use_watch and not args.offline_sample else None)

    res = build(args, watch=watch)
    lines = render_txt(res)
    if args.dry_run:
        print("\n".join(lines))
        print("\n[dry-run] no files written")
        return 0

    dataio.write_json(OUT_JSON, res)
    dataio.write_text(OUT_TXT, lines)
    dataio.write_json(SOURCE_AUDIT_JSON, _source_audit_artifact(res))
    dataio.write_text(SOURCE_AUDIT_TXT, _render_source_audit(res))
    if watch is not None:
        dataio.write_json(WATCH_JSON, watch)
        dataio.write_text(WATCH_TXT, [
            f"== SOCIAL ATTENTION WATCH UNIVERSE — {watch['generated_at']} ==",
            f"size={watch['size']} cap={watch['cap']} (hard max {watch['hard_max']})",
            "source_counts: " + ", ".join(f"{k}={v}" for k, v in watch["source_counts"].items()),
            "", " ".join(watch["universe"]),
        ])
    _write_doc()
    # Append-only, idempotent per day: skip if today's asof already historized.
    # Offline fixtures never touch the real ledger.
    if not args.offline_sample and res["asof_date"] not in _existing_asof_dates(HISTORY):
        dataio.append_jsonl(HISTORY, _history_rows(res))
    if args.print:
        print("\n".join(lines))
    print(f"\nwrote {dataio.rel_to_repo(OUT_JSON)} · {dataio.rel_to_repo(OUT_TXT)} · "
          f"profile={res['profile']} leads={res['counts']['leads']} "
          f"social_led={res['counts']['social_led']}")
    return 0


def _render_source_audit(res: Dict[str, Any]) -> List[str]:
    L = [
        f"== SOCIAL ATTENTION SOURCE AUDIT ({res['version']}) — {res.get('generated_at')} ==",
        f"profile={res.get('profile')}  (Reddit intentionally DISABLED — not required)",
        "",
    ]
    for k, v in (res.get("source_health") or {}).items():
        L.append(f"  {k:<14} {v.get('health'):<14} items={v.get('items'):<4} {v.get('detail')}")
    if res.get("source_errors"):
        L += ["", "errors:"] + [f"  - {e}" for e in res["source_errors"][:8]]
    return L


if __name__ == "__main__":
    raise SystemExit(main())
