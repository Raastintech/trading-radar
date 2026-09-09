#!/usr/bin/env python3
"""research/alpha_heat_radar.py — Alpha Heat Radar V0.

RESEARCH-ONLY / CACHE-ONLY.  A topic-first alpha discovery surface:

    Market Heat  ->  Topic Shock  ->  Ticker Mapping  ->  Alpha Research Alert

This is deliberately NOT a rebuild of the Social Attention Radar or the
News/Social Catalyst Radar (``social_attention_radar.py`` /
``social_arb_radar.py``).  Those engines already compute per-TICKER
attention velocity, novelty, and confirmation.  This module's only new
contribution is the missing layer above them: grouping today's already-
observed chatter by TOPIC first (so a cluster of names moving together on
one theme is visible even when no single ticker looks loud on its own),
then re-attaching tickers, then cross-referencing every mapped ticker
against the existing High-Conviction / Emerging Outlier / Alpha Focus /
Alpha Discovery Board / sector-leadership / regime artifacts to decide
whether today's heat is worth a human's time.

It reads only artifacts that are ALREADY on disk.  It makes zero
provider/API calls, zero DB writes, and computes zero new per-ticker
attention-velocity math (that stays owned by ``social_attention_radar.py``).
The only new persisted state is a topic-level, day-level history file used
solely to answer "is this topic novel or recycled" — everything else is a
same-run cross-reference over already-published sidecars.

Sources read (all cache-only):
  - cache/research/social_attention_radar_latest.json   (prod leads)
  - cache/research/social_attention_v11_latest.json      (shadow leads)
  - cache/research/social_arb_latest.json                (news/catalyst confirm)
  - cache/research/high_conviction_alpha_latest.json
  - cache/research/emerging_outlier_watch_latest.json
  - cache/research/alpha_focus_latest.json
  - cache/research/alpha_discovery_board_latest.json
  - cache/research/sector_leadership_latest.json         (optional, may be stale)
  - cache/research/regime_forecast_latest.json           (optional)

Outputs:
  cache/research/alpha_heat_radar_latest.json
  cache/research/alpha_heat_radar_latest.md
  logs/alpha_heat_radar_latest.txt
  data/research/alpha_heat_topic_history.jsonl   (append-only, 1 row/topic/day)

Output labels (research routing only — NEVER buy/sell/execute/approved):
  RESEARCH_NOW · WATCH_FOR_RESET · CONFIRMATION_ONLY · EXHAUSTION_RISK ·
  SOCIAL_NOISE · REDFLAG_NOISE · INSUFFICIENT_MAPPING_CONFIDENCE

Doctrine: aggressive in discovery, conservative in labeling.  Hot does not
mean buy.  Social-only does not mean alpha.  Crowded/exhaustion is risk
unless proven otherwise.  This module never changes scanner logic, scores,
rankings, gates, thresholds, HC/EO rules, Alpha Focus rules, or program
verdicts, and never routes anything into production.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, pstdev
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from core.quarantined_surfaces import is_current as quarantine_is_current

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

VERSION = "ALPHA_HEAT_RADAR_V0"

# ── artifact paths (repo-relative; joined against ``root`` for testability) ──
SOCIAL_ATTENTION_REL = Path("cache/research/social_attention_radar_latest.json")
SOCIAL_ATTENTION_V11_REL = Path("cache/research/social_attention_v11_latest.json")
SOCIAL_ARB_REL = Path("cache/research/social_arb_latest.json")
HIGH_CONVICTION_REL = Path("cache/research/high_conviction_alpha_latest.json")
EMERGING_OUTLIER_REL = Path("cache/research/emerging_outlier_watch_latest.json")
ALPHA_FOCUS_REL = Path("cache/research/alpha_focus_latest.json")
ALPHA_BOARD_REL = Path("cache/research/alpha_discovery_board_latest.json")
SECTOR_LEADERSHIP_REL = Path("cache/research/sector_leadership_latest.json")
REGIME_FORECAST_REL = Path("cache/research/regime_forecast_latest.json")

OUT_JSON_REL = Path("cache/research/alpha_heat_radar_latest.json")
OUT_MD_REL = Path("cache/research/alpha_heat_radar_latest.md")
OUT_TXT_REL = Path("logs/alpha_heat_radar_latest.txt")
HISTORY_REL = Path("data/research/alpha_heat_topic_history.jsonl")

DISCLAIMER = (
    "RESEARCH-ONLY topic-first alpha discovery radar.  Labels are research "
    "routing only — NOT buy/sell/trade signals, NOT paper signals, NOT trade "
    "proposals.  Hot does not mean buy; social-only does not mean alpha; "
    "crowded/exhaustion is treated as risk unless proven otherwise.  No "
    "execution/governance/gate/live-capital/scanner/scoring changes; no DB "
    "writes; cache-only inputs."
)

# ── canonical topic taxonomy (Task: merged superset of the two existing,
# divergent theme tables in social_attention_radar.py and social_arb_radar.py,
# plus a "Space" topic — both source taxonomies name it, but only
# social_attention_radar.py's THEME_IMPACT actually baskets it).  Kept local
# and self-contained (same "duplicate a small constant table per research
# module" convention already used by both source files) rather than importing
# either module's heavier taxonomy, so this module stays a pure cache reader
# with no coupling to social_arb_radar.py's core.fmp_client import.
# ``base_novelty`` is a 0-7 scale: how surprising a spike in this topic is.
# Topics that are ALWAYS loud (Macro/Policy) get a low ceiling so they can
# never look "novel" just for existing.
TOPIC_RULES: Dict[str, Dict[str, Any]] = {
    "AI/Data Center": {
        "terms": ("artificial intelligence", " ai ", "ai chip", "gpu",
                  "data center", "datacenter", "accelerator", "inference", "server"),
        "tickers": ("NVDA", "AMD", "AVGO", "SMCI", "VRT", "ORCL"),
        "base_novelty": 4.0,
    },
    "Semiconductors": {
        "terms": ("semiconductor", "chip", "foundry", "hbm", "wafer", "memory chip"),
        "tickers": ("NVDA", "AMD", "AVGO", "TSM", "MU", "AMAT", "LRCX"),
        "base_novelty": 5.0,
    },
    "Power/Nuclear": {
        "terms": ("nuclear", "uranium", "small modular reactor", "smr",
                  "power demand", "grid", "electricity"),
        "tickers": ("CCJ", "CEG", "VST", "TLN", "OKLO", "SMR", "GEV", "NNE"),
        "base_novelty": 7.0,
    },
    "Crypto": {
        "terms": ("bitcoin", "crypto", "stablecoin", "ethereum", "digital asset"),
        "tickers": ("COIN", "MSTR", "MARA", "RIOT", "HOOD"),
        "base_novelty": 5.0,
    },
    "Defense": {
        "terms": ("defense", "missile", "drone", "aerospace", "pentagon", "dod contract"),
        "tickers": ("LMT", "RTX", "NOC", "BA", "KTOS", "RCAT", "AXON"),
        "base_novelty": 6.0,
    },
    "Space": {
        "terms": ("space", "satellite", "launch", "rocket", "lunar", "orbital"),
        "tickers": ("RKLB", "LUNR", "ASTS", "ACHR", "JOBY"),
        "base_novelty": 6.0,
    },
    "Obesity/Healthcare": {
        "terms": ("glp-1", "obesity", "weight loss", "weight-loss", "drug trial"),
        "tickers": ("LLY", "NVO", "HIMS", "UNH", "MRNA", "PFE"),
        "base_novelty": 5.0,
    },
    "Cybersecurity": {
        "terms": ("cybersecurity", "breach", "ransomware", "zero trust", "cloud security"),
        "tickers": ("CRWD", "PANW", "NET", "ZS", "FTNT"),
        "base_novelty": 5.0,
    },
    "Consumer/App Demand": {
        "terms": ("app downloads", "streaming", "e-commerce", "delivery",
                  "advertising demand", "consumer demand"),
        "tickers": ("AMZN", "SHOP", "DASH", "UBER", "ABNB", "RDDT", "SNAP"),
        "base_novelty": 5.0,
    },
    "Macro/Policy": {
        "terms": ("tariff", "rate cut", "inflation", "fed", "fomc",
                  "treasury yield", "export control"),
        "tickers": (),
        "base_novelty": 3.0,
    },
}
MAX_BASE_NOVELTY = max(r["base_novelty"] for r in TOPIC_RULES.values())

# Index/ETF cashtags that are never a single-name research target — filtered
# out of any ticker candidate list regardless of source.
NON_ACTIONABLE_TICKERS = frozenset({
    "SPY", "QQQ", "DIA", "IWM", "VXX", "TLT", "HYG", "LQD", "SMH", "XLK",
    "XLV", "XLF", "XLE", "XLY", "XLP", "XLI", "XLU", "XLB", "XLRE", "XLC",
})

# ── thresholds (pre-registered here; documented, not tuned to any one day) ──
MIN_MAPPING_CONFIDENCE = 0.40      # below this: INSUFFICIENT_MAPPING_CONFIDENCE
HOT_VELOCITY_SCORE = 60.0          # topic-level, 0-100 (same scale as ticker attention_velocity_score)
HOT_ACCELERATION = 1.3             # topic-level acceleration_ratio (matches social_attention_radar RISING_ACCEL)
EXCESSIVE_CHATTER_APPEARANCES = 20  # ticker's own history_appearances (social_attention lead)
REPEAT_TOPIC_RATIO_HIGH = 0.6       # topic present in >=60% of last 20 sessions => "always loud"
DILUTION_REDFLAG_PCT = 5.0          # EO dilution_3q_pct above this (dilutive) => redflag
LOW_FLOAT_RELVOL_PROXY = 3.0        # price_overlay.rel_volume above this => pump-risk proxy (informational only)
V11_NOISE_SCORE_THRESHOLD = 60.0
EXTENDED_STATES = frozenset({"EXTENDED", "PARABOLIC", "STRETCHED"})
DETERIORATION_RISK_REDFLAG = frozenset({"MEDIUM", "HIGH"})
NOVELTY_LOOKBACK_SESSIONS = 20

FRESHNESS_MAX_HOURS: Dict[str, float] = {
    "social_attention_radar": 30.0,
    "social_attention_v11": 30.0,
    "social_arb": 30.0,
    "high_conviction_alpha": 30.0,
    "emerging_outlier_watch": 30.0,
    "alpha_focus": 30.0,
    "alpha_discovery_board": 30.0,
    "sector_leadership": 192.0,   # weekly-ish cadence; informational only
    "regime_forecast": 30.0,
}

LABEL_RESEARCH_NOW = "RESEARCH_NOW"
LABEL_WATCH_FOR_RESET = "WATCH_FOR_RESET"
LABEL_CONFIRMATION_ONLY = "CONFIRMATION_ONLY"
LABEL_EXHAUSTION_RISK = "EXHAUSTION_RISK"
LABEL_SOCIAL_NOISE = "SOCIAL_NOISE"
LABEL_REDFLAG_NOISE = "REDFLAG_NOISE"
LABEL_INSUFFICIENT_MAPPING = "INSUFFICIENT_MAPPING_CONFIDENCE"


# ── tiny cache-only I/O helpers (same pattern as every sibling module) ──────
def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _load_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    except Exception:
        return None


def _load_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def _append_jsonl(path: Path, rows: List[Dict[str, Any]]) -> int:
    if not rows:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, default=str) + "\n")
    return len(rows)


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, default=str), encoding="utf-8")


def _parse_ts(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _artifact_freshness(name: str, doc: Optional[Dict[str, Any]], now: datetime) -> Dict[str, Any]:
    present = doc is not None
    ts = None
    if present:
        for field in ("generated_at", "built_at", "artifact_timestamp"):
            ts = _parse_ts(doc.get(field))
            if ts:
                break
    age_hours = round((now - ts).total_seconds() / 3600.0, 2) if ts else None
    max_hours = FRESHNESS_MAX_HOURS.get(name, 30.0)
    stale = bool(present and (age_hours is None or age_hours > max_hours))
    return {
        "present": present,
        "generated_at": ts.isoformat() if ts else None,
        "age_hours": age_hours,
        "max_age_hours": max_hours,
        "stale": stale if present else None,
    }


# ── Step A/B — topic detection + rollup of already-computed ticker heat ────
def detect_topics(text: str) -> List[str]:
    """Which curated topics does this text's keywords touch (0+)."""
    if not text:
        return []
    low = f" {text.lower()} "
    return [topic for topic, rule in TOPIC_RULES.items()
            if any(term in low for term in rule["terms"])]


_TICKER_TO_TOPICS: Dict[str, List[str]] = {}
for _topic, _rule in TOPIC_RULES.items():
    for _t in _rule["tickers"]:
        _TICKER_TO_TOPICS.setdefault(_t, []).append(_topic)


def ticker_topic_membership(ticker: str) -> List[str]:
    return _TICKER_TO_TOPICS.get((ticker or "").upper(), [])


def _lead_topics(lead: Dict[str, Any]) -> Set[str]:
    """Union of text-keyword topics and ticker-basket topics for one lead.
    Ticker-basket membership only ever affects TOPIC attribution (does this
    lead's chatter plausibly belong to topic X) — it never invents or
    upgrades a ticker mapping; ticker confidence always comes from the
    lead's own ``best_confidence``/``best_mapping_method``."""
    topics: Set[str] = set()
    for text in lead.get("sample_texts") or []:
        topics.update(detect_topics(text))
    topics.update(ticker_topic_membership(lead.get("ticker", "")))
    return topics


def _index_leads(leads: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {l["ticker"]: l for l in (leads or []) if l.get("ticker")}


def _rollup_topics(sa_leads: Dict[str, Dict[str, Any]],
                    v11_leads: Dict[str, Dict[str, Any]],
                    arb_by_ticker: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Group already-observed per-ticker chatter by topic.  Returns
    topic -> {contributing: [...], mention_volume, velocity_score,
    acceleration_ratio, source_diversity_kinds, contributing_ticker_count}.
    Only topics with >=1 contributing ticker are returned."""
    by_topic: Dict[str, List[Dict[str, Any]]] = {t: [] for t in TOPIC_RULES}

    seen_tickers: Set[str] = set()
    for ticker, lead in sa_leads.items():
        topics = _lead_topics(lead)
        if not topics:
            continue
        seen_tickers.add(ticker)
        for topic in topics:
            by_topic[topic].append({
                "ticker": ticker,
                "source_kind": "social_attention",
                "mention_count_24h": lead.get("mention_count_24h") or 0,
                "attention_velocity_score": lead.get("attention_velocity_score") or 0.0,
                "acceleration_ratio": lead.get("acceleration_ratio") or 0.0,
            })

    for ticker, lead in v11_leads.items():
        if ticker in seen_tickers:
            continue  # prod lead already covers this ticker; v11 is shadow-only confirmation
        topics = _lead_topics(lead)
        if not topics:
            continue
        for topic in topics:
            by_topic[topic].append({
                "ticker": ticker,
                "source_kind": "social_attention_v11_shadow",
                "mention_count_24h": lead.get("mention_count_24h") or 0,
                "attention_velocity_score": lead.get("attention_velocity_score") or 0.0,
                "acceleration_ratio": lead.get("acceleration_ratio") or 0.0,
            })

    out: Dict[str, Dict[str, Any]] = {}
    for topic, contributing in by_topic.items():
        if not contributing:
            continue
        weights = [max(c["mention_count_24h"], 1) for c in contributing]
        total_w = sum(weights)
        velocity_score = sum(c["attention_velocity_score"] * w for c, w in zip(contributing, weights)) / total_w
        acceleration_ratio = sum(c["acceleration_ratio"] * w for c, w in zip(contributing, weights)) / total_w
        mention_volume = sum(c["mention_count_24h"] for c in contributing)
        source_kinds = {c["source_kind"] for c in contributing}
        arb_confirmed_tickers = [c["ticker"] for c in contributing if c["ticker"] in arb_by_ticker]
        if arb_confirmed_tickers:
            source_kinds.add("social_arb_confirmed")
        out[topic] = {
            "topic": topic,
            "contributing": contributing,
            "mention_volume": mention_volume,
            "velocity_score": round(velocity_score, 1),
            "acceleration_ratio": round(acceleration_ratio, 2),
            "source_diversity_kinds": sorted(source_kinds),
            "contributing_ticker_count": len({c["ticker"] for c in contributing}),
        }
    return out


# ── Step C — Novelty (needs the topic-day historizer) ───────────────────────
def _existing_topic_dates(history: List[Dict[str, Any]], topic: str) -> Set[str]:
    return {r.get("asof_date") for r in history if r.get("topic") == topic}


def compute_topic_novelty(topic: str, asof_date: str, today_volume: int,
                           history: List[Dict[str, Any]]) -> Dict[str, Any]:
    base_novelty = TOPIC_RULES[topic]["base_novelty"]
    novelty_ceiling = _clamp(base_novelty / MAX_BASE_NOVELTY, 0.0, 1.0) * 100.0

    prior = sorted(
        (r for r in history if r.get("topic") == topic and str(r.get("asof_date")) < asof_date),
        key=lambda r: str(r.get("asof_date")),
    )
    n_prior_sessions = len(prior)
    first_seen_date = prior[0]["asof_date"] if prior else asof_date
    last_n = prior[-NOVELTY_LOOKBACK_SESSIONS:]
    appearances = sum(1 for r in last_n if (r.get("mention_volume") or 0) > 0)
    appearance_ratio = (appearances / len(last_n)) if last_n else 0.0

    novelty_score = round(_clamp(novelty_ceiling * (1.0 - appearance_ratio), 0.0, 100.0), 1)
    is_recycled = n_prior_sessions >= 5 and appearance_ratio >= REPEAT_TOPIC_RATIO_HIGH

    sudden_change_z: Optional[float] = None
    if n_prior_sessions >= 3:
        counts = [float(r.get("mention_volume") or 0) for r in prior]
        mu = mean(counts)
        sigma = pstdev(counts) if len(counts) > 1 else 0.0
        if sigma > 0:
            sudden_change_z = round((today_volume - mu) / sigma, 2)
        else:
            sudden_change_z = 5.0 if today_volume > mu else 0.0

    return {
        "novelty_score": novelty_score,
        "first_seen_date": first_seen_date,
        "n_prior_sessions": n_prior_sessions,
        "appearance_ratio_last_20": round(appearance_ratio, 3),
        "is_recycled_topic": is_recycled,
        "sudden_change_z": sudden_change_z,
        "history_maturity": "OK" if n_prior_sessions >= 3 else "INSUFFICIENT_HISTORY",
    }


# ── Step F/E — cross-reference indices over already-published artifacts ────
def _index_hc(doc: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for bucket in ("shortlist", "quality_but_extended", "improving_but_unproven", "rejected"):
        for row in (doc or {}).get(bucket, []) or []:
            if row.get("ticker"):
                out[row["ticker"]] = {**row, "_hc_bucket": bucket}
    return out


def _index_eo(doc: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {r["ticker"]: r for r in (doc or {}).get("watch", []) or [] if r.get("ticker")}


def _index_alpha_focus(doc: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for bucket in ("review_now", "higher_risk_eo_review", "wait_for_reset", "deprioritized_by_focus_rule"):
        for row in (doc or {}).get(bucket, []) or []:
            if row.get("ticker"):
                out.setdefault(row["ticker"], {**row, "_focus_bucket": bucket})
    return out


def _index_board(doc: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {r["ticker"]: r for r in (doc or {}).get("items", []) or [] if r.get("ticker")}


def _index_social_arb(doc: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for bucket in ("reviewed_candidates", "items"):
        for row in (doc or {}).get(bucket, []) or []:
            if row.get("ticker"):
                out.setdefault(row["ticker"], row)
    return out


def compute_noise_flags(*, sa_lead: Optional[Dict[str, Any]], v11_lead: Optional[Dict[str, Any]],
                        hc_row: Optional[Dict[str, Any]], eo_row: Optional[Dict[str, Any]],
                        arb_row: Optional[Dict[str, Any]], board_row: Optional[Dict[str, Any]],
                        focus_row: Optional[Dict[str, Any]] = None
                        ) -> Dict[str, Any]:
    flags: List[str] = []
    exhaustion = False
    redflag = False

    crowd_stage = (sa_lead or {}).get("crowd_stage")
    parabolic = bool(((sa_lead or {}).get("price_overlay") or {}).get("parabolic"))
    v11_noise_score = ((v11_lead or {}).get("noise") or {}).get("noise_score")
    if crowd_stage in ("EXHAUSTION_RISK", "VIRAL_CROWDING"):
        exhaustion = True
        flags.append(f"CROWD_STAGE_{crowd_stage}")
    if parabolic:
        exhaustion = True
        flags.append("PARABOLIC_PRICE")
    if v11_noise_score is not None and v11_noise_score >= V11_NOISE_SCORE_THRESHOLD:
        exhaustion = True
        flags.append(f"V11_HIGH_NOISE_SCORE_{v11_noise_score:.0f}")

    unique_source_count = (sa_lead or {}).get("unique_source_count")
    no_fundamental_support = not any([hc_row, eo_row, arb_row, board_row, focus_row])
    if unique_source_count is not None and unique_source_count <= 1 and not arb_row:
        flags.append("SINGLE_SOURCE_SPIKE")
    if no_fundamental_support:
        flags.append("SOCIAL_ONLY_NO_NEWS_OR_FUNDAMENTAL_SUPPORT")

    history_appearances = (sa_lead or {}).get("history_appearances")
    if history_appearances is not None and history_appearances >= EXCESSIVE_CHATTER_APPEARANCES:
        flags.append(f"EXCESSIVE_REPEATED_CHATTER_{history_appearances}X")

    eo_risk = (eo_row or {}).get("business_deterioration_risk")
    hc_risk = (hc_row or {}).get("dead_horse_risk")
    dilution = (eo_row or {}).get("dilution_3q_pct")
    if eo_risk in DETERIORATION_RISK_REDFLAG or hc_risk in DETERIORATION_RISK_REDFLAG:
        redflag = True
        flags.append("DETERIORATION_RISK_REDFLAG")
    if dilution is not None and dilution > DILUTION_REDFLAG_PCT:
        redflag = True
        flags.append(f"DILUTION_REDFLAG_{dilution:.1f}PCT")

    rel_volume = ((sa_lead or {}).get("price_overlay") or {}).get("rel_volume")
    if rel_volume is not None and rel_volume >= LOW_FLOAT_RELVOL_PROXY:
        flags.append(f"HIGH_RELATIVE_VOLUME_PROXY_ONLY_{rel_volume:.1f}X")

    return {
        "flags": flags,
        "exhaustion_risk": exhaustion,
        "redflag": redflag,
        "no_fundamental_support": no_fundamental_support,
    }


def compute_alpha_fit(*, focus_row: Optional[Dict[str, Any]], hc_row: Optional[Dict[str, Any]],
                      eo_row: Optional[Dict[str, Any]], board_row: Optional[Dict[str, Any]],
                      sector_doc: Optional[Dict[str, Any]], regime_doc: Optional[Dict[str, Any]]
                      ) -> Dict[str, Any]:
    positives: List[str] = []
    score = 0.0
    is_wait_for_reset = False

    if focus_row:
        if focus_row.get("is_profitable"):
            positives.append("profitable_quality")
            score += 15
        if focus_row.get("is_reset_or_watch_for_entry"):
            positives.append("reset_or_watch_for_entry")
            score += 10
        if focus_row.get("is_high_conviction"):
            positives.append("high_conviction")
            score += 30
        if focus_row.get("is_emerging_outlier"):
            positives.append("emerging_outlier")
            score += 20
        if focus_row.get("is_mid_or_known_cap"):
            positives.append("mid_or_known_cap")
            score += 5
        is_wait_for_reset = focus_row.get("_focus_bucket") == "wait_for_reset"
    else:
        if hc_row and hc_row.get("classification") == "HIGH_CONVICTION":
            positives.append("high_conviction")
            score += 30
        if eo_row:
            positives.append("emerging_outlier")
            score += 20

    extension_state = (hc_row or {}).get("extension_state")
    gatekeeper_status = (board_row or {}).get("gatekeeper_status")
    validator_state = str((board_row or {}).get("validator_state") or "")
    is_extended_or_blocked = (
        is_wait_for_reset
        or extension_state in EXTENDED_STATES
        or gatekeeper_status == "BLOCK"
        or "extend" in validator_state.lower()
    )

    same_session = None
    for row in (focus_row, hc_row, eo_row, board_row):
        if row and row.get("same_session") is not None:
            same_session = row.get("same_session")
            break

    sector_etf = (hc_row or {}).get("sector_etf")
    leading = [s for s in (sector_doc or {}).get("leading_sectors_20d", []) or [] if isinstance(s, str)]
    return {
        "score": round(min(score, 100.0), 1),
        "positive_reasons": positives,
        "is_extended_or_blocked": is_extended_or_blocked,
        "same_session_clean": same_session,
        "sector_etf": sector_etf,
        "sector_leading": bool(sector_etf) and sector_etf in leading,
        "current_regime": ((regime_doc or {}).get("headline") or {}).get("current_regime"),
    }


# ── Step G — label assignment (conservative-by-design) ──────────────────────
def assign_label(*, mapping_confidence: Optional[float], noise: Dict[str, Any],
                 alpha_fit: Dict[str, Any], topic_is_hot: bool,
                 has_arb_confirmation: bool) -> Tuple[str, str]:
    """Returns (label, one_sentence_reason).  Preconditions are checked in a
    fixed, most-conservative-wins order — every RESEARCH_NOW requires that
    NONE of the negative conditions above it fired."""
    if mapping_confidence is None or mapping_confidence < MIN_MAPPING_CONFIDENCE:
        return (LABEL_INSUFFICIENT_MAPPING,
                f"ticker mapping confidence {mapping_confidence!r} is below the "
                f"{MIN_MAPPING_CONFIDENCE} floor — topic is hot but the ticker link is weak.")
    if noise["redflag"]:
        return (LABEL_REDFLAG_NOISE,
                "business-deterioration or dilution red flag present; chatter does not "
                "override fundamental risk.")
    if noise["exhaustion_risk"]:
        return (LABEL_EXHAUSTION_RISK,
                "crowd stage / price action / shadow-noise score indicates the move is "
                "already crowded or parabolic — treat as risk, not opportunity.")
    if alpha_fit["is_extended_or_blocked"] and alpha_fit["score"] > 0:
        return (LABEL_WATCH_FOR_RESET,
                "positive research profile (" + ", ".join(alpha_fit["positive_reasons"]) +
                ") but extended/gate-blocked/needs-reset today — revisit after a pullback.")
    if topic_is_hot and alpha_fit["score"] > 0 and not noise["flags"]:
        return (LABEL_RESEARCH_NOW,
                "topic is accelerating, ticker mapping is confident, and the name has a "
                "positive research reason (" + ", ".join(alpha_fit["positive_reasons"]) + ") "
                "with no noise/red flags today.")
    if alpha_fit["score"] > 0:
        return (LABEL_CONFIRMATION_ONLY,
                "positive research reason (" + ", ".join(alpha_fit["positive_reasons"]) + ") "
                "already established, but the topic is not fresh/accelerating today.")
    if has_arb_confirmation:
        return (LABEL_CONFIRMATION_ONLY,
                "already confirmed by the News/Social Catalyst Radar with tape/options "
                "support, but no independent positive research reason or topic acceleration today.")
    if noise["no_fundamental_support"]:
        return (LABEL_SOCIAL_NOISE,
                "social-only chatter with no news, fundamental, or research-program "
                "corroboration found.")
    return (LABEL_SOCIAL_NOISE, "no positive research reason and no fresh topic acceleration today.")


# ── build ────────────────────────────────────────────────────────────────────
def build(root: Optional[Path] = None, now: Optional[datetime] = None) -> Dict[str, Any]:
    root = Path(root) if root else REPO_ROOT
    now = now or _utcnow()

    sa_doc = _load_json(root / SOCIAL_ATTENTION_REL)
    # Social Attention V11 is SUPERSEDED (2026-09-09 drift audit): it has no
    # cadence, its artifacts went 12.6 days stale while the nightly V0 radar ran,
    # and two disagreeing social surfaces with no stated winner is worse than
    # one. The file is preserved and still read here, but it is dropped unless
    # core.quarantined_surfaces says it may speak as current — so this module
    # sees exactly one social lane. Wiring V11 to a cadence is the one change
    # that should lift it, in that module, not here.
    _v11_raw = _load_json(root / SOCIAL_ATTENTION_V11_REL)
    v11_doc = _v11_raw if quarantine_is_current("social_attention_v11", _v11_raw) else None
    arb_doc = _load_json(root / SOCIAL_ARB_REL)
    hc_doc = _load_json(root / HIGH_CONVICTION_REL)
    eo_doc = _load_json(root / EMERGING_OUTLIER_REL)
    focus_doc = _load_json(root / ALPHA_FOCUS_REL)
    board_doc = _load_json(root / ALPHA_BOARD_REL)
    sector_doc = _load_json(root / SECTOR_LEADERSHIP_REL)
    regime_doc = _load_json(root / REGIME_FORECAST_REL)

    source_dependencies = {
        "social_attention_radar": _artifact_freshness("social_attention_radar", sa_doc, now),
        "social_attention_v11": _artifact_freshness("social_attention_v11", v11_doc, now),
        "social_arb": _artifact_freshness("social_arb", arb_doc, now),
        "high_conviction_alpha": _artifact_freshness("high_conviction_alpha", hc_doc, now),
        "emerging_outlier_watch": _artifact_freshness("emerging_outlier_watch", eo_doc, now),
        "alpha_focus": _artifact_freshness("alpha_focus", focus_doc, now),
        "alpha_discovery_board": _artifact_freshness("alpha_discovery_board", board_doc, now),
        "sector_leadership": _artifact_freshness("sector_leadership", sector_doc, now),
        "regime_forecast": _artifact_freshness("regime_forecast", regime_doc, now),
    }

    if sa_doc is None and arb_doc is None:
        return {
            "kind": "alpha_heat_radar",
            "version": VERSION,
            "generated_at": now.isoformat(),
            "research_only": True,
            "promote_to_signal": False,
            "disclaimer": DISCLAIMER,
            "fallback": "MISSING_ARTIFACT",
            "missing_artifacts": [str(SOCIAL_ATTENTION_REL), str(SOCIAL_ARB_REL)],
            "source_dependencies": source_dependencies,
            "guardrails": _guardrails(),
        }

    asof_date = (sa_doc or arb_doc or {}).get("asof_date") or now.date().isoformat()
    if "T" in str(asof_date):
        asof_date = str(asof_date)[:10]

    sa_leads = _index_leads((sa_doc or {}).get("leads") or [])
    v11_leads = _index_leads((v11_doc or {}).get("leads") or [])
    arb_by_ticker = _index_social_arb(arb_doc)
    hc_by_ticker = _index_hc(hc_doc)
    eo_by_ticker = _index_eo(eo_doc)
    focus_by_ticker = _index_alpha_focus(focus_doc)
    board_by_ticker = _index_board(board_doc)

    topic_rollup = _rollup_topics(sa_leads, v11_leads, arb_by_ticker)

    history = _load_jsonl(root / HISTORY_REL)
    topics_out: List[Dict[str, Any]] = []
    alerts: List[Dict[str, Any]] = []
    new_history_rows: List[Dict[str, Any]] = []

    for topic in sorted(topic_rollup, key=lambda t: -topic_rollup[t]["velocity_score"]):
        roll = topic_rollup[topic]
        novelty = compute_topic_novelty(topic, asof_date, roll["mention_volume"], history)
        topic_is_hot = (roll["velocity_score"] >= HOT_VELOCITY_SCORE
                        and roll["acceleration_ratio"] >= HOT_ACCELERATION)

        topics_out.append({
            "topic": topic,
            "mention_volume": roll["mention_volume"],
            "velocity_score": roll["velocity_score"],
            "acceleration_ratio": roll["acceleration_ratio"],
            "source_diversity_kinds": roll["source_diversity_kinds"],
            "contributing_ticker_count": roll["contributing_ticker_count"],
            "topic_is_hot": topic_is_hot,
            **novelty,
        })

        if asof_date not in _existing_topic_dates(history, topic):
            new_history_rows.append({
                "asof_date": asof_date,
                "version": VERSION,
                "topic": topic,
                "mention_volume": roll["mention_volume"],
                "contributing_ticker_count": roll["contributing_ticker_count"],
                "velocity_score": roll["velocity_score"],
            })

        for contrib in roll["contributing"]:
            ticker = contrib["ticker"]
            sa_lead = sa_leads.get(ticker)
            v11_lead = v11_leads.get(ticker)
            source_lead = sa_lead or v11_lead or {}
            mapping_confidence = source_lead.get("best_confidence")
            mapping_method = source_lead.get("best_mapping_method")
            if ticker in NON_ACTIONABLE_TICKERS:
                continue

            hc_row = hc_by_ticker.get(ticker)
            eo_row = eo_by_ticker.get(ticker)
            arb_row = arb_by_ticker.get(ticker)
            board_row = board_by_ticker.get(ticker)
            focus_row = focus_by_ticker.get(ticker)

            noise = compute_noise_flags(sa_lead=sa_lead, v11_lead=v11_lead, hc_row=hc_row,
                                        eo_row=eo_row, arb_row=arb_row, board_row=board_row,
                                        focus_row=focus_row)
            alpha_fit = compute_alpha_fit(focus_row=focus_row, hc_row=hc_row, eo_row=eo_row,
                                          board_row=board_row, sector_doc=sector_doc,
                                          regime_doc=regime_doc)
            label, reason = assign_label(
                mapping_confidence=mapping_confidence, noise=noise, alpha_fit=alpha_fit,
                topic_is_hot=topic_is_hot, has_arb_confirmation=bool(arb_row))

            scanner_overlaps: List[str] = []
            if hc_row:
                scanner_overlaps.append(f"HIGH_CONVICTION:{hc_row.get('classification')}")
            if eo_row:
                scanner_overlaps.append(f"EMERGING_OUTLIER:{eo_row.get('v1_classification')}")
            if focus_row:
                scanner_overlaps.append(f"ALPHA_FOCUS:{focus_row.get('_focus_bucket')}")
            if board_row:
                scanner_overlaps.append(f"ALPHA_BOARD:{board_row.get('action_label')}"
                                        f"/{board_row.get('gatekeeper_status')}")
            if arb_row:
                scanner_overlaps.append(f"SOCIAL_ARB:{arb_row.get('bucket')}"
                                        f"({arb_row.get('confidence')})")

            alerts.append({
                "topic": topic,
                "ticker": ticker,
                "heat_score": roll["velocity_score"],
                "novelty_score": novelty["novelty_score"],
                "source_spread": roll["source_diversity_kinds"],
                "mapping_confidence": mapping_confidence,
                "mapping_method": mapping_method,
                "shadow_only": contrib["source_kind"] == "social_attention_v11_shadow",
                "alpha_fit_score": alpha_fit["score"],
                "noise_risk_flags": noise["flags"],
                "scanner_overlaps": scanner_overlaps,
                "operator_label": label,
                "reason": reason,
                "same_session_clean": alpha_fit["same_session_clean"],
                "sector_leading": alpha_fit["sector_leading"],
                "current_regime": alpha_fit["current_regime"],
                "research_only": True,
                "disclaimer": DISCLAIMER,
            })

    alerts.sort(key=lambda a: (
        {LABEL_RESEARCH_NOW: 0, LABEL_WATCH_FOR_RESET: 1, LABEL_CONFIRMATION_ONLY: 2,
         LABEL_EXHAUSTION_RISK: 3, LABEL_REDFLAG_NOISE: 4, LABEL_INSUFFICIENT_MAPPING: 5,
         LABEL_SOCIAL_NOISE: 6}.get(a["operator_label"], 9),
        -a["heat_score"],
    ))

    counts: Dict[str, int] = {}
    for a in alerts:
        counts[a["operator_label"]] = counts.get(a["operator_label"], 0) + 1

    return {
        "kind": "alpha_heat_radar",
        "version": VERSION,
        "generated_at": now.isoformat(),
        "asof_date": asof_date,
        "research_only": True,
        "promote_to_signal": False,
        "disclaimer": DISCLAIMER,
        "guardrails": _guardrails(),
        "source_dependencies": source_dependencies,
        "topics": topics_out,
        "alerts": alerts,
        "counts": {"topics_active": len(topics_out), "alerts": len(alerts), **counts},
        "_new_history_rows": new_history_rows,  # consumed by main(); not part of the public schema
    }


def _guardrails() -> Dict[str, bool]:
    return {
        "research_only": True,
        "promote_to_signal": False,
        "cache_only_inputs": True,
        "no_provider_calls": True,
        "no_db_writes": True,
        "no_selection_change": True,
        "no_scanner_logic_change": True,
        "no_score_or_ranking_change": True,
        "no_gate_or_threshold_change": True,
        "no_high_conviction_or_emerging_outlier_or_alpha_focus_rule_change": True,
        "no_trade_recommendation": True,
        "hot_does_not_mean_buy": True,
    }


# ── render ───────────────────────────────────────────────────────────────────
def render_txt(res: Dict[str, Any]) -> List[str]:
    if res.get("fallback"):
        return [f"== ALPHA HEAT RADAR ({res['version']}) — {res['generated_at']} ==",
                f"fallback={res['fallback']} missing={res.get('missing_artifacts')}"]
    c = res["counts"]
    L = [
        f"== ALPHA HEAT RADAR ({res['version']}) — {res['generated_at']} ==",
        res["disclaimer"],
        f"asof={res['asof_date']}  topics_active={c['topics_active']}  alerts={c['alerts']}",
        "counts: " + ", ".join(f"{k}={v}" for k, v in c.items()
                               if k not in ("topics_active", "alerts")),
        "",
        f"{'topic':<22}{'heat':>6}{'accel':>7}{'novelty':>9}{'tickers':>9}{'hot':>6}",
    ]
    for t in res["topics"][:15]:
        L.append(f"{t['topic']:<22}{t['velocity_score']:>6.1f}{t['acceleration_ratio']:>7.2f}"
                 f"{t['novelty_score']:>9.1f}{t['contributing_ticker_count']:>9}"
                 f"{'YES' if t['topic_is_hot'] else '':>6}")
    L += ["", f"{'ticker':<7}{'topic':<22}{'label':<28}{'heat':>6}{'fit':>5}{'map':>6}"]
    for a in res["alerts"][:40]:
        L.append(f"{a['ticker']:<7}{a['topic']:<22}{a['operator_label']:<28}"
                 f"{a['heat_score']:>6.1f}{a['alpha_fit_score']:>5.0f}"
                 f"{(a['mapping_confidence'] or 0):>6.2f}")
    return L


def render_md(res: Dict[str, Any]) -> List[str]:
    if res.get("fallback"):
        return [f"# Alpha Heat Radar — {res['generated_at']}",
                "", f"**fallback:** `{res['fallback']}` (missing {res.get('missing_artifacts')})"]
    c = res["counts"]
    L = [
        f"# Alpha Heat Radar ({res['version']}) — {res['asof_date']}",
        "",
        f"_{res['disclaimer']}_",
        "",
        f"**Topics active:** {c['topics_active']}  **Alerts:** {c['alerts']}",
        "",
        "| label | count |",
        "|---|---|",
    ]
    for k, v in c.items():
        if k in ("topics_active", "alerts"):
            continue
        L.append(f"| {k} | {v} |")
    L += ["", "## Topics", "", "| topic | heat | accel | novelty | tickers | hot |", "|---|---|---|---|---|---|"]
    for t in res["topics"]:
        L.append(f"| {t['topic']} | {t['velocity_score']:.1f} | {t['acceleration_ratio']:.2f} | "
                 f"{t['novelty_score']:.1f} | {t['contributing_ticker_count']} | "
                 f"{'YES' if t['topic_is_hot'] else ''} |")
    L += ["", "## Alerts", "",
          "| ticker | topic | label | heat | novelty | fit | mapping | reason |",
          "|---|---|---|---|---|---|---|---|"]
    for a in res["alerts"]:
        L.append(f"| {a['ticker']} | {a['topic']} | {a['operator_label']} | "
                 f"{a['heat_score']:.1f} | {a['novelty_score']:.1f} | {a['alpha_fit_score']:.0f} | "
                 f"{(a['mapping_confidence'] or 0):.2f} | {a['reason']} |")
    return L


def write_artifacts(res: Dict[str, Any], root: Optional[Path] = None) -> Dict[str, str]:
    root = Path(root) if root else REPO_ROOT
    new_rows = res.pop("_new_history_rows", [])
    json_path = root / OUT_JSON_REL
    md_path = root / OUT_MD_REL
    txt_path = root / OUT_TXT_REL
    _write_json(json_path, res)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("\n".join(render_md(res)) + "\n", encoding="utf-8")
    txt_path.parent.mkdir(parents=True, exist_ok=True)
    txt_path.write_text("\n".join(render_txt(res)) + "\n", encoding="utf-8")
    if new_rows:
        _append_jsonl(root / HISTORY_REL, new_rows)
    return {"json": str(json_path), "markdown": str(md_path), "text": str(txt_path)}


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Alpha Heat Radar V0 — topic-first alpha discovery (research-only, cache-only)")
    ap.add_argument("--root", default=None, help=argparse.SUPPRESS)
    ap.add_argument("--dry-run", action="store_true", help="print, write nothing")
    ap.add_argument("--print", action="store_true")
    args = ap.parse_args(argv)
    root = Path(args.root) if args.root else REPO_ROOT

    res = build(root)
    lines = render_txt(res)
    if args.dry_run:
        print("\n".join(lines))
        print("\n[dry-run] no files written")
        return 0

    paths = write_artifacts(res, root)
    if args.print:
        print("\n".join(lines))
    print(f"wrote {paths['json']}\nwrote {paths['markdown']}\nwrote {paths['text']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
