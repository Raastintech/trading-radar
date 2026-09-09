#!/usr/bin/env python3
"""research/topic_shock_detector.py — Topic Shock Detector V0.

RESEARCH-ONLY / CACHE-ONLY.  The next layer UNDER Alpha Heat Radar
(``research/alpha_heat_radar.py``): where that module groups already-
observed ticker chatter into a fixed 10-topic dictionary (classification),
this module detects EMERGENT topics from raw text — whatever phrase
cluster is actually hot today, whether or not it matches a pre-enumerated
theme.

Pipeline:
    Corpus assembly -> syndication/template suppression -> phrase
    extraction (document frequency) -> cluster formation -> phrase-day
    history (novelty) -> ticker mapping (never invented) -> alpha-fit /
    noise overlay (reused from Alpha Heat Radar, not duplicated) ->
    conservative topic label.

Sources read (all cache-only, zero provider calls):
  - cache/research/social_arb_raw_latest.json        (pre-filter raw corpus —
    the ONLY artifact with real multi-source, multi-ticker raw text; this is
    what makes emergent (non-dictionary) topic detection possible at all)
  - cache/research/social_attention_radar_latest.json
  - cache/research/social_attention_v11_latest.json  (optional — read if present)
  - cache/research/social_arb_latest.json            (FINAL filtered items —
    used only for the "already confirmed" cross-reference, never for corpus text)
  - every artifact Alpha Heat Radar already reads for its alpha-fit overlay
    (HC / EO / Alpha Focus / Alpha Discovery Board / sector leadership / regime)

A design note on why a naive implementation is dangerous: an ad hoc probe
against today's cache showed that raw cross-document n-gram frequency
counting, run without a syndication filter, manufactures FALSE cross-ticker
topics out of recurring boilerplate — law-firm "class action / investor
alert" solicitation press releases (PRNewswire) fire against a different,
unrelated ticker every day and were the single highest-document-frequency
phrase cluster in the corpus; "undiscovered breakout" newsletter templates
and Estimize/MarketBeat auto-tweet templates did the same.  Step 2 below
(``suppress_templates``) exists specifically to catch this BEFORE phrase
extraction, and reports what it caught rather than silently dropping it.

Outputs:
  cache/research/topic_shock_detector_latest.json
  cache/research/topic_shock_detector_latest.md
  logs/topic_shock_detector_latest.txt
  data/research/topic_shock_phrase_history.jsonl   (append-only, 1 row/phrase/day)

Output labels (research routing only — NEVER buy/sell/execute/approved),
in fixed, most-conservative-wins priority order:
  TOPIC_MAPPING_WEAK > TOPIC_REDFLAG_NOISE > TOPIC_EXHAUSTION_RISK >
  TOPIC_OLD_RECHURN > TOPIC_WATCH_FOR_RESET > TOPIC_RESEARCH_NOW >
  TOPIC_CONFIRMATION_ONLY

This module never changes scanner logic, scores, rankings, gates,
thresholds, HC/EO rules, Alpha Focus rules, or program verdicts; never
routes into the dashboard/MCP/cron/run_research_cycle.sh; never creates a
trade signal; never touches broker/paper/live trading; never mutates an
existing evidence ledger.  It reuses Alpha Heat Radar's
``compute_alpha_fit``/``compute_noise_flags`` (and its HC/EO/Focus/Board
index helpers, path constants, and thresholds) by import rather than
re-deriving that cross-reference logic.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, pstdev
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.quarantined_surfaces import is_current as quarantine_is_current  # noqa: E402
from research import alpha_heat_radar as ahr  # noqa: E402  (deliberate reuse, see module docstring)

VERSION = "TOPIC_SHOCK_DETECTOR_V0"

# ── artifact paths ────────────────────────────────────────────────────────────
# The raw pre-filter news corpus is unique to this module (Alpha Heat Radar
# never reads it).  Every other input path is imported from alpha_heat_radar
# so both modules always read the same file for the same purpose.
SOCIAL_ARB_RAW_REL = Path("cache/research/social_arb_raw_latest.json")

OUT_JSON_REL = Path("cache/research/topic_shock_detector_latest.json")
OUT_MD_REL = Path("cache/research/topic_shock_detector_latest.md")
OUT_TXT_REL = Path("logs/topic_shock_detector_latest.txt")
HISTORY_REL = Path("data/research/topic_shock_phrase_history.jsonl")

DISCLAIMER = (
    "RESEARCH-ONLY topic shock detector.  Labels are research routing only — "
    "NOT buy/sell/trade signals, NOT paper signals, NOT trade proposals.  Hot "
    "does not mean buy; a detected phrase cluster is not alpha until "
    "corroborated; syndicated/template chatter is noise, not a topic.  No "
    "execution/governance/gate/live-capital/scanner/scoring/routing changes; "
    "no DB writes; cache-only inputs."
)

# ── thresholds (shared ones imported from Alpha Heat Radar so both modules
# use one source of truth; module-specific ones defined and documented here) ──
MIN_MAPPING_CONFIDENCE = ahr.MIN_MAPPING_CONFIDENCE          # 0.40
HOT_ACCELERATION = ahr.HOT_ACCELERATION                       # 1.3
REPEAT_RATIO_HIGH = ahr.REPEAT_TOPIC_RATIO_HIGH                # 0.6
NOVELTY_LOOKBACK_SESSIONS = ahr.NOVELTY_LOOKBACK_SESSIONS

MIN_CLUSTER_DOCUMENT_FREQUENCY = 3   # a phrase needs >=3 distinct surviving docs to seed a cluster
MIN_CLUSTER_TICKER_BREADTH = 2       # and must span >=2 DISTINCT tickers — a "topic" is cross-name
                                      # by definition; single-ticker repetition is Alpha Heat Radar's
                                      # job, not this module's (this is what filtered out the
                                      # AAPL-only "position apple"/"aapl sold"/"mac mini" fragments
                                      # a same-day probe produced without this guard)
MERGE_JACCARD_THRESHOLD = 0.5        # overlapping phrase doc-sets above this merge into one cluster
HOT_HEAT_SCORE = 60.0                # 0-100; document_count*12 capped at 100 (needs df>=5)
SECTOR_BASKET_CONFIDENCE = 0.35      # below the 0.40 floor by design — basket alone should not qualify
MAX_EVIDENCE_PHRASES = 6
MAX_SUPPRESSED_EXAMPLES = 3

# ── stopwords for n-gram extraction (small, curated — not exhaustive) ──────
STOPWORDS = frozenset("""
the and for with from that this will has have its are was were been more than
into over about after before their them they you your our ceo inc corp stock
stocks shares market today day week new says could would may can not but all
out get one two three million billion percent pct year years also said report
reports according per share company companies best top buy sell hold long
short now still just like every much most many first last next big small
good great here how why what who where when going back few days let see half
position target invested highs lows bearish bullish transcript recap smallcap
holdings sold bought calls puts catalyst momentum resistance support session
trade trading portfolio watchlist alert alerts update updates raises down any
news time dreams pipe expect expects expected range bias
""".split())

URL_RE = re.compile(r"https?://\S+|www\.\S+")
TOKEN_RE = re.compile(r"[a-z0-9\-]+")


# ── Step 2 — syndication / template suppression ─────────────────────────────
# Learned from a read-only probe against a real cache snapshot (see module
# docstring): these are recurring boilerplate templates, not topics.  A doc
# matching any pattern is suppressed BEFORE phrase extraction, never used to
# seed a cluster, and reported in ``suppressed_as_template`` — never silently
# dropped.
TEMPLATE_PATTERNS: Dict[str, re.Pattern] = {
    "LAW_FIRM_CLASS_ACTION": re.compile(
        r"class action|securities fraud|shareholder rights|lead plaintiff|"
        r"securities class|investor (?:alert|deadline)", re.IGNORECASE),
    "INVESTOR_LOSS_SOLICITATION": re.compile(
        r"investors? with[^.]{0,20}losses|opportunity to lead|"
        r"encourages? [^.]{0,40}investors to (?:inquire|contact)|"
        r"law firm (?:is )?investigat", re.IGNORECASE),
    "UNDISCOVERED_BREAKOUT_NEWSLETTER": re.compile(
        r"undiscovered (?:breakout|gem)s?|hidden gem stock|breakout candidate",
        re.IGNORECASE),
    "ESTIMIZE_MARKETBEAT_BOT": re.compile(
        r"estimize\.com|marketbeat\.com|historical amp metric|"
        r"metric name eps|bias (?:bullish|bearish)[^.]{0,20}expected range|"
        r"recap reported gaap", re.IGNORECASE),
    "GENERIC_LISTICLE_ROUNDUP": re.compile(
        r"\b\d+\s+(?:best|top)\b|stock market today|stocks mixed|"
        r"dow futures|nasdaq futures|market wrap|closing bell|before the bell",
        re.IGNORECASE),
    "INSTITUTIONAL_HOLDINGS_BOILERPLATE": re.compile(
        r"wealth management|capital management|management llc|advisors llc|"
        r"llc (?:bought|sold|acquired|increased|decreased|trimmed|boosted)|"
        r"(?:boosted|trimmed|acquired) (?:its )?(?:stake|position)", re.IGNORECASE),
}


# ── tiny cache-only I/O helpers (same convention as every sibling module) ──
def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _strip_urls(text: str) -> str:
    return URL_RE.sub(" ", html.unescape(text or ""))


def _tokens(text: str) -> List[str]:
    return [w for w in TOKEN_RE.findall(text.lower())
            if w not in STOPWORDS and len(w) > 2]


def _ngrams(tokens: List[str], n: int) -> Set[str]:
    return {" ".join(tokens[i:i + n]) for i in range(len(tokens) - n + 1)}


def _jaccard(a: Set[str], b: Set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


# ── Step 1 — corpus assembly ─────────────────────────────────────────────────
def _snippet(doc_id: str, text: str, *, domain: str, pipeline: str,
            tickers_hint: Sequence[str], ticker_confidence: Optional[Dict[str, float]] = None
            ) -> Dict[str, Any]:
    return {
        "doc_id": doc_id,
        "text": text or "",
        "domain": domain or "unknown",
        "pipeline": pipeline,
        "tickers_hint": sorted(set(t for t in (tickers_hint or []) if t)),
        "ticker_confidence": ticker_confidence or {},
    }


def collect_snippets(arb_raw_doc: Optional[Dict[str, Any]],
                     sa_doc: Optional[Dict[str, Any]],
                     v11_doc: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    snippets: List[Dict[str, Any]] = []

    for i, item in enumerate((arb_raw_doc or {}).get("normalized_items") or []):
        title = item.get("title") or ""
        if not title:
            continue
        evidence = item.get("ticker_evidence") or {}
        conf: Dict[str, float] = {}
        for ticker, ev in evidence.items():
            if ev.get("ambiguous_hit"):
                conf[ticker] = 0.20
            elif ev.get("direct_symbol"):
                conf[ticker] = 0.85
            elif ev.get("is_subject") or ev.get("in_title"):
                conf[ticker] = 0.65
            elif ev.get("in_body"):
                conf[ticker] = 0.50
            else:
                conf[ticker] = 0.35
        snippets.append(_snippet(
            f"arb:{i}", title, domain=item.get("source") or "unknown",
            pipeline="social_arb_raw", tickers_hint=item.get("tickers_mentioned") or [],
            ticker_confidence=conf))

    for doc, pipeline in ((sa_doc, "social_attention"), (v11_doc, "social_attention_v11")):
        for lead in (doc or {}).get("leads") or []:
            ticker = lead.get("ticker")
            if not ticker:
                continue
            best_conf = lead.get("best_confidence")
            for j, text in enumerate(lead.get("sample_texts") or []):
                snippets.append(_snippet(
                    f"{pipeline}:{ticker}:{j}", text, domain=f"social:{ticker}",
                    pipeline=pipeline, tickers_hint=[ticker],
                    ticker_confidence={ticker: best_conf} if best_conf is not None else {}))

    return snippets


# ── Step 2 — syndication / template suppression ─────────────────────────────
def suppress_templates(snippets: Sequence[Dict[str, Any]]
                       ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    survivors: List[Dict[str, Any]] = []
    agg: Dict[str, Dict[str, Any]] = {}

    for s in snippets:
        text = s["text"]
        matched_name: Optional[str] = None
        matched_text: Optional[str] = None
        for name, pattern in TEMPLATE_PATTERNS.items():
            m = pattern.search(text)
            if m:
                matched_name, matched_text = name, m.group(0).lower()
                break
        if matched_name is None:
            survivors.append(s)
            continue
        bucket = agg.setdefault(matched_name, {
            "matched_pattern": matched_name,
            "matched_phrases": Counter(),
            "source_domains": set(),
            "tickers": set(),
            "document_count": 0,
        })
        bucket["matched_phrases"][matched_text] += 1
        bucket["source_domains"].add(s["domain"])
        bucket["tickers"].update(s["tickers_hint"])
        bucket["document_count"] += 1

    suppressed: List[Dict[str, Any]] = []
    for name, bucket in agg.items():
        top_phrase, _ = bucket["matched_phrases"].most_common(1)[0]
        suppressed.append({
            "matched_pattern": name,
            "matched_phrase": top_phrase,
            "example_matched_phrases": [p for p, _ in bucket["matched_phrases"].most_common(MAX_SUPPRESSED_EXAMPLES)],
            "source_domains": sorted(bucket["source_domains"])[:15],
            "tickers": sorted(bucket["tickers"])[:25],
            "document_count": bucket["document_count"],
        })
    suppressed.sort(key=lambda r: -r["document_count"])
    return survivors, suppressed


# ── Step 3 — phrase extraction (document frequency, deduped within a doc) ──
def build_doc_phrase_index(survivors: Sequence[Dict[str, Any]]) -> Dict[str, Set[str]]:
    """doc_id -> set of distinct bigrams/trigrams in that doc (deduplicated
    within the document, as required — a phrase repeated 3x in one headline
    counts once toward that phrase's document frequency)."""
    index: Dict[str, Set[str]] = {}
    for s in survivors:
        toks = _tokens(_strip_urls(s["text"]))
        phrases = _ngrams(toks, 2) | _ngrams(toks, 3)
        if phrases:
            index[s["doc_id"]] = phrases
    return index


def document_frequency(doc_index: Dict[str, Set[str]]) -> Counter:
    df: Counter = Counter()
    for phrases in doc_index.values():
        df.update(phrases)  # each phrase counted once per doc (set, not list)
    return df


# ── Step 4 — cluster formation ───────────────────────────────────────────────
def _doc_tickers(doc_ids: Set[str], survivors_by_id: Dict[str, Dict[str, Any]]) -> Set[str]:
    out: Set[str] = set()
    for d in doc_ids:
        doc = survivors_by_id.get(d)
        if doc:
            out.update(doc["tickers_hint"])
    return out


def form_clusters(doc_index: Dict[str, Set[str]], df: Counter,
                  survivors_by_id: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Greedy, deterministic clustering: phrases are processed by document
    frequency descending (ties broken alphabetically); a phrase either seeds
    a new cluster or merges into an existing one if its supporting-document
    set overlaps that cluster's by >= MERGE_JACCARD_THRESHOLD.  A phrase must
    also span >= MIN_CLUSTER_TICKER_BREADTH distinct tickers to seed/join a
    cluster — a "topic" is cross-name by construction; a phrase repeating
    across one ticker's own chatter is not a topic shock, it's just that
    ticker being talked about (Alpha Heat Radar's job).  An emergent phrase
    that matches no known topic keeps its own name — it is never forced into
    a pre-enumerated bucket.  As a final pass, clusters that independently
    resolve to the SAME known topic (via ``ahr.detect_topics``) are merged —
    otherwise the same theme can fragment into several near-duplicate
    clusters purely because their seed phrases didn't share enough raw
    documents to Jaccard-merge on their own."""
    candidates = sorted(
        ((phrase, count) for phrase, count in df.items() if count >= MIN_CLUSTER_DOCUMENT_FREQUENCY),
        key=lambda kv: (-kv[1], kv[0]),
    )
    phrase_docs = {
        phrase: {doc_id for doc_id, phrases in doc_index.items() if phrase in phrases}
        for phrase, _ in candidates
    }
    candidates = [(p, c) for p, c in candidates
                  if len(_doc_tickers(phrase_docs[p], survivors_by_id)) >= MIN_CLUSTER_TICKER_BREADTH]

    clusters: List[Dict[str, Any]] = []
    for phrase, _ in candidates:
        docs = phrase_docs[phrase]
        merged = False
        for cluster in clusters:
            if _jaccard(docs, cluster["doc_ids"]) >= MERGE_JACCARD_THRESHOLD:
                cluster["doc_ids"] |= docs
                cluster["evidence_phrases"].append(phrase)
                merged = True
                break
        if not merged:
            clusters.append({"seed_phrase": phrase, "doc_ids": set(docs), "evidence_phrases": [phrase]})

    prelim: List[Dict[str, Any]] = []
    for cluster in clusters:
        docs = [survivors_by_id[d] for d in cluster["doc_ids"] if d in survivors_by_id]
        if not docs:
            continue
        known_topics = Counter()
        for d in docs:
            known_topics.update(ahr.detect_topics(d["text"]))
        known_topic = known_topics.most_common(1)[0][0] if known_topics else None
        prelim.append({"seed_phrase": cluster["seed_phrase"], "known_topic": known_topic,
                       "evidence_phrases": cluster["evidence_phrases"], "doc_ids": cluster["doc_ids"]})

    merged_by_topic: Dict[str, Dict[str, Any]] = {}
    final: List[Dict[str, Any]] = []
    for cluster in prelim:
        kt = cluster["known_topic"]
        if kt is None:
            final.append(cluster)
            continue
        if kt not in merged_by_topic:
            merged_by_topic[kt] = cluster
            final.append(cluster)
        else:
            target = merged_by_topic[kt]
            target["doc_ids"] |= cluster["doc_ids"]
            target["evidence_phrases"] += cluster["evidence_phrases"]

    out: List[Dict[str, Any]] = []
    for cluster in final:
        docs = [survivors_by_id[d] for d in cluster["doc_ids"] if d in survivors_by_id]
        if not docs:
            continue
        name = cluster["known_topic"] or cluster["seed_phrase"]
        out.append({
            "name": name,
            "known_topic": cluster["known_topic"],
            "evidence_phrases": cluster["evidence_phrases"][:MAX_EVIDENCE_PHRASES],
            "documents": docs,
            "document_count": len(docs),
            "source_domains": sorted({d["domain"] for d in docs}),
            "pipelines": sorted({d["pipeline"] for d in docs}),
        })
    out.sort(key=lambda c: -c["document_count"])
    return out


# ── Step 5 — phrase-day history / novelty (cold start = honest, not confident) ──
def _existing_phrase_dates(history: List[Dict[str, Any]], name: str) -> Set[str]:
    return {r.get("asof_date") for r in history if r.get("name") == name}


def compute_cluster_novelty(name: str, asof_date: str, today_count: int,
                            history: List[Dict[str, Any]], base_novelty: float,
                            max_base_novelty: float) -> Dict[str, Any]:
    """Same math as Alpha Heat Radar's ``compute_topic_novelty``, generalized
    to take an explicit ``base_novelty`` so it works for emergent cluster
    names that are not in Alpha Heat Radar's fixed TOPIC_RULES (those default
    to the maximum ceiling — an unclassified topic is, by construction, not a
    known "always loud" one).  Kept as a local, self-contained copy rather
    than importing Alpha Heat Radar's version (which is hard-bound to its own
    TOPIC_RULES dict and would KeyError on an emergent name) — this is pure
    novelty math, not the HC/EO/Focus/Board cross-reference logic the task
    asked not to duplicate."""
    novelty_ceiling = _clamp(base_novelty / max_base_novelty, 0.0, 1.0) * 100.0

    prior = sorted(
        (r for r in history if r.get("name") == name and str(r.get("asof_date")) < asof_date),
        key=lambda r: str(r.get("asof_date")),
    )
    n_prior_sessions = len(prior)
    first_seen_date = prior[0]["asof_date"] if prior else asof_date
    last_n = prior[-NOVELTY_LOOKBACK_SESSIONS:]
    appearances = sum(1 for r in last_n if (r.get("document_count") or 0) > 0)
    appearance_ratio = (appearances / len(last_n)) if last_n else 0.0

    novelty_score = round(_clamp(novelty_ceiling * (1.0 - appearance_ratio), 0.0, 100.0), 1)
    is_recycled = n_prior_sessions >= 5 and appearance_ratio >= REPEAT_RATIO_HIGH

    acceleration_ratio: Optional[float] = None
    if prior:
        baseline = mean(float(r.get("document_count") or 0) for r in prior[-7:])
        acceleration_ratio = round(today_count / baseline, 2) if baseline > 0 else None

    sudden_change_z: Optional[float] = None
    if n_prior_sessions >= 3:
        counts = [float(r.get("document_count") or 0) for r in prior]
        mu = mean(counts)
        sigma = pstdev(counts) if len(counts) > 1 else 0.0
        if sigma > 0:
            sudden_change_z = round((today_count - mu) / sigma, 2)
        else:
            sudden_change_z = 5.0 if today_count > mu else 0.0

    return {
        "novelty_score": novelty_score,
        "first_seen_date": first_seen_date,
        "n_prior_sessions": n_prior_sessions,
        "appearance_ratio_last_20": round(appearance_ratio, 3),
        "is_recycled_topic": is_recycled,
        "acceleration_ratio": acceleration_ratio,
        "sudden_change_z": sudden_change_z,
        "history_maturity": "OK" if n_prior_sessions >= 3 else "INSUFFICIENT_HISTORY",
    }


# ── Step 6 — ticker mapping (never invented) ────────────────────────────────
def map_cluster_tickers(cluster: Dict[str, Any]) -> List[Dict[str, Any]]:
    best: Dict[str, Tuple[float, str]] = {}

    for doc in cluster["documents"]:
        for ticker, conf in (doc.get("ticker_confidence") or {}).items():
            ticker = (ticker or "").upper()
            if not ticker or ticker in ahr.NON_ACTIONABLE_TICKERS:
                continue
            method = "explicit_evidence" if doc["pipeline"] == "social_arb_raw" else "social_attention_lead"
            if ticker not in best or conf > best[ticker][0]:
                best[ticker] = (conf, method)
        for ticker in doc.get("tickers_hint") or []:
            ticker = ticker.upper()
            if ticker in ahr.NON_ACTIONABLE_TICKERS or ticker in best:
                continue
            best[ticker] = (0.35, "mentioned_no_confidence_score")

    if cluster.get("known_topic"):
        basket = ahr.TOPIC_RULES.get(cluster["known_topic"], {}).get("tickers", ())
        for ticker in basket:
            if ticker in ahr.NON_ACTIONABLE_TICKERS:
                continue
            if ticker not in best or best[ticker][0] < SECTOR_BASKET_CONFIDENCE:
                if ticker not in best:
                    best[ticker] = (SECTOR_BASKET_CONFIDENCE, "sector_basket")

    return [
        {"ticker": t, "confidence": round(c, 2), "method": m,
         "qualifies": c >= MIN_MAPPING_CONFIDENCE}
        for t, (c, m) in sorted(best.items(), key=lambda kv: -kv[1][0])
    ]


# ── Step 7 — alpha-fit / noise overlay (reused, not duplicated) ────────────
def _overlay_ticker(ticker: str, *, sa_leads: Dict[str, Any], v11_leads: Dict[str, Any],
                    hc_by_ticker: Dict[str, Any], eo_by_ticker: Dict[str, Any],
                    focus_by_ticker: Dict[str, Any], board_by_ticker: Dict[str, Any],
                    arb_by_ticker: Dict[str, Any], sector_doc: Optional[Dict[str, Any]],
                    regime_doc: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    sa_lead = sa_leads.get(ticker)
    v11_lead = v11_leads.get(ticker)
    hc_row = hc_by_ticker.get(ticker)
    eo_row = eo_by_ticker.get(ticker)
    focus_row = focus_by_ticker.get(ticker)
    board_row = board_by_ticker.get(ticker)
    arb_row = arb_by_ticker.get(ticker)

    noise = ahr.compute_noise_flags(sa_lead=sa_lead, v11_lead=v11_lead, hc_row=hc_row,
                                    eo_row=eo_row, arb_row=arb_row, board_row=board_row,
                                    focus_row=focus_row)
    alpha_fit = ahr.compute_alpha_fit(focus_row=focus_row, hc_row=hc_row, eo_row=eo_row,
                                      board_row=board_row, sector_doc=sector_doc,
                                      regime_doc=regime_doc)
    scanner_overlaps: List[str] = []
    if hc_row:
        scanner_overlaps.append(f"HIGH_CONVICTION:{hc_row.get('classification')}")
    if eo_row:
        scanner_overlaps.append(f"EMERGING_OUTLIER:{eo_row.get('v1_classification')}")
    if focus_row:
        scanner_overlaps.append(f"ALPHA_FOCUS:{focus_row.get('_focus_bucket')}")
    if board_row:
        scanner_overlaps.append(f"ALPHA_BOARD:{board_row.get('action_label')}/{board_row.get('gatekeeper_status')}")
    if arb_row:
        scanner_overlaps.append(f"SOCIAL_ARB:{arb_row.get('bucket')}({arb_row.get('confidence')})")

    return {"noise": noise, "alpha_fit": alpha_fit, "scanner_overlaps": scanner_overlaps,
            "already_confirmed": bool(arb_row)}


# ── Step 8 — label assignment (fixed, most-conservative-wins priority) ─────
LABEL_MAPPING_WEAK = "TOPIC_MAPPING_WEAK"
LABEL_REDFLAG_NOISE = "TOPIC_REDFLAG_NOISE"
LABEL_EXHAUSTION_RISK = "TOPIC_EXHAUSTION_RISK"
LABEL_OLD_RECHURN = "TOPIC_OLD_RECHURN"
LABEL_WATCH_FOR_RESET = "TOPIC_WATCH_FOR_RESET"
LABEL_RESEARCH_NOW = "TOPIC_RESEARCH_NOW"
LABEL_CONFIRMATION_ONLY = "TOPIC_CONFIRMATION_ONLY"


def assign_topic_label(*, mapped_tickers: List[Dict[str, Any]], overlays: Dict[str, Dict[str, Any]],
                       novelty: Dict[str, Any], heat_score: float) -> Tuple[str, str, Optional[Dict[str, Any]]]:
    qualifying = [t for t in mapped_tickers if t["qualifies"]]
    if not qualifying:
        return (LABEL_MAPPING_WEAK,
                "no mapped ticker reaches the "
                f"{MIN_MAPPING_CONFIDENCE} confidence floor — topic chatter exists but the "
                "ticker link is too weak to act on.", None)

    scored = [(t, overlays[t["ticker"]]) for t in qualifying]
    if any(ov["noise"]["redflag"] for _, ov in scored):
        return (LABEL_REDFLAG_NOISE,
                "at least one mapped ticker carries a business-deterioration or dilution "
                "red flag; chatter does not override fundamental risk.", None)
    if any(ov["noise"]["exhaustion_risk"] for _, ov in scored):
        return (LABEL_EXHAUSTION_RISK,
                "at least one mapped ticker's crowd stage / price action / shadow-noise "
                "score indicates the move is already crowded or parabolic.", None)
    if novelty["is_recycled_topic"]:
        return (LABEL_OLD_RECHURN,
                "this phrase cluster has recurred across most recent sessions — old, "
                "recycled chatter, not new information.", None)

    best = max(scored, key=lambda ts: (ts[1]["alpha_fit"]["score"], ts[0]["confidence"]))
    best_ticker, best_overlay = best
    fit = best_overlay["alpha_fit"]

    if fit["score"] > 0 and fit["is_extended_or_blocked"]:
        return (LABEL_WATCH_FOR_RESET,
                f"{best_ticker['ticker']} has a positive research profile "
                f"({', '.join(fit['positive_reasons']) or 'n/a'}) but is extended/gate-blocked "
                "today — revisit after a pullback.", best_ticker)

    accel = novelty.get("acceleration_ratio")
    is_hot = (heat_score >= HOT_HEAT_SCORE and accel is not None and accel >= HOT_ACCELERATION)
    # Scoped to the CITED (best) ticker only — a topic can legitimately map
    # several tickers, and an unrelated co-mapped ticker having no
    # fundamental support of its own must not block RESEARCH_NOW for the one
    # ticker that actually has a clean, positive story today.
    if is_hot and fit["score"] > 0 and not best_overlay["noise"]["flags"]:
        return (LABEL_RESEARCH_NOW,
                f"topic is accelerating (x{accel}), mapping is confident, and "
                f"{best_ticker['ticker']} has a positive research reason "
                f"({', '.join(fit['positive_reasons'])}) with no noise flags today.", best_ticker)

    return (LABEL_CONFIRMATION_ONLY,
            "mapped ticker(s) already known/confirmed or no fresh acceleration today — "
            "nothing new for manual research right now.", best_ticker)


# ── source dependencies / freshness (informational) ─────────────────────────
def _source_dependencies(now: datetime, **docs: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    return {name: ahr._artifact_freshness(name, doc, now) for name, doc in docs.items()}


# ── optional sympathy-basket price confirmation (informational only — never
# feeds the label ladder) ────────────────────────────────────────────────────
def price_confirmation_for_basket(known_topic: str) -> Dict[str, Any]:
    basket = ahr.TOPIC_RULES.get(known_topic, {}).get("tickers", ())
    if not basket:
        return {"available": False}
    try:
        import pandas as pd  # local import: heavy dep only needed for this optional overlay
        from research.scanner_truth import dataio
    except Exception:
        return {"available": False}

    spy = dataio.load_prices("SPY")
    if spy is None or len(spy) < 6:
        return {"available": False}
    spy_ret = float(spy["close"].iloc[-1] / spy["close"].iloc[-6] - 1.0)

    rets: List[float] = []
    for ticker in basket:
        df = dataio.load_prices(ticker)
        if df is None or len(df) < 6:
            continue
        rets.append(float(df["close"].iloc[-1] / df["close"].iloc[-6] - 1.0))
    if not rets:
        return {"available": False}

    return {
        "available": True,
        "basket_median_return_5d": round(float(pd.Series(rets).median()), 4),
        "spy_return_5d": round(spy_ret, 4),
        "breadth_positive_count": sum(1 for r in rets if r > 0),
        "breadth_total": len(rets),
    }


# ── build ────────────────────────────────────────────────────────────────────
def build(root: Optional[Path] = None, now: Optional[datetime] = None,
         with_price_confirmation: bool = False) -> Dict[str, Any]:
    root = Path(root) if root else REPO_ROOT
    now = now or _utcnow()

    arb_raw_doc = ahr._load_json(root / SOCIAL_ARB_RAW_REL)
    sa_doc = ahr._load_json(root / ahr.SOCIAL_ATTENTION_REL)
    # Social Attention V11 is SUPERSEDED (2026-09-09 drift audit): it has no
    # cadence, its artifacts went 12.6 days stale while the nightly V0 radar ran,
    # and two disagreeing social surfaces with no stated winner is worse than
    # one. The file is preserved and still read here, but it is dropped unless
    # core.quarantined_surfaces says it may speak as current — so this module
    # sees exactly one social lane. Wiring V11 to a cadence is the one change
    # that should lift it, in that module, not here.
    _v11_raw = ahr._load_json(root / ahr.SOCIAL_ATTENTION_V11_REL)
    v11_doc = _v11_raw if quarantine_is_current("social_attention_v11", _v11_raw) else None
    arb_final_doc = ahr._load_json(root / ahr.SOCIAL_ARB_REL)
    hc_doc = ahr._load_json(root / ahr.HIGH_CONVICTION_REL)
    eo_doc = ahr._load_json(root / ahr.EMERGING_OUTLIER_REL)
    focus_doc = ahr._load_json(root / ahr.ALPHA_FOCUS_REL)
    board_doc = ahr._load_json(root / ahr.ALPHA_BOARD_REL)
    sector_doc = ahr._load_json(root / ahr.SECTOR_LEADERSHIP_REL)
    regime_doc = ahr._load_json(root / ahr.REGIME_FORECAST_REL)

    source_dependencies = _source_dependencies(
        now, social_arb_raw=arb_raw_doc, social_attention_radar=sa_doc,
        social_attention_v11=v11_doc, social_arb=arb_final_doc,
        high_conviction_alpha=hc_doc, emerging_outlier_watch=eo_doc,
        alpha_focus=focus_doc, alpha_discovery_board=board_doc,
        sector_leadership=sector_doc, regime_forecast=regime_doc)

    if arb_raw_doc is None and sa_doc is None:
        return {
            "kind": "topic_shock_detector",
            "version": VERSION,
            "generated_at": now.isoformat(),
            "research_only": True,
            "promote_to_signal": False,
            "no_routing_change": True,
            "hot_does_not_mean_buy": True,
            "disclaimer": DISCLAIMER,
            "fallback": "MISSING_ARTIFACT",
            "missing_artifacts": [str(SOCIAL_ARB_RAW_REL), str(ahr.SOCIAL_ATTENTION_REL)],
            "source_dependencies": source_dependencies,
            "guardrails": _guardrails(),
        }

    asof_date = str((arb_raw_doc or {}).get("built_at") or (sa_doc or {}).get("asof_date")
                    or now.date().isoformat())[:10]

    snippets = collect_snippets(arb_raw_doc, sa_doc, v11_doc)
    survivors, suppressed_as_template = suppress_templates(snippets)
    survivors_by_id = {s["doc_id"]: s for s in survivors}
    doc_index = build_doc_phrase_index(survivors)
    df = document_frequency(doc_index)
    clusters = form_clusters(doc_index, df, survivors_by_id)

    sa_leads = ahr._index_leads((sa_doc or {}).get("leads") or [])
    v11_leads = ahr._index_leads((v11_doc or {}).get("leads") or [])
    arb_by_ticker = ahr._index_social_arb(arb_final_doc)
    hc_by_ticker = ahr._index_hc(hc_doc)
    eo_by_ticker = ahr._index_eo(eo_doc)
    focus_by_ticker = ahr._index_alpha_focus(focus_doc)
    board_by_ticker = ahr._index_board(board_doc)

    history = ahr._load_jsonl(root / HISTORY_REL)
    new_history_rows: List[Dict[str, Any]] = []
    alerts: List[Dict[str, Any]] = []

    for cluster in clusters:
        name = cluster["name"]
        heat_score = round(min(100.0, cluster["document_count"] * 12.0), 1)
        base_novelty = (ahr.TOPIC_RULES.get(cluster["known_topic"], {}).get("base_novelty")
                        if cluster["known_topic"] else ahr.MAX_BASE_NOVELTY)
        novelty = compute_cluster_novelty(name, asof_date, cluster["document_count"], history,
                                          base_novelty, ahr.MAX_BASE_NOVELTY)

        if asof_date not in _existing_phrase_dates(history, name):
            new_history_rows.append({
                "asof_date": asof_date, "version": VERSION, "name": name,
                "known_topic": cluster["known_topic"],
                "document_count": cluster["document_count"],
            })

        mapped_tickers = map_cluster_tickers(cluster)
        overlays = {
            t["ticker"]: _overlay_ticker(
                t["ticker"], sa_leads=sa_leads, v11_leads=v11_leads, hc_by_ticker=hc_by_ticker,
                eo_by_ticker=eo_by_ticker, focus_by_ticker=focus_by_ticker,
                board_by_ticker=board_by_ticker, arb_by_ticker=arb_by_ticker,
                sector_doc=sector_doc, regime_doc=regime_doc)
            for t in mapped_tickers if t["qualifies"]
        }
        label, reason, best_ticker = assign_topic_label(
            mapped_tickers=mapped_tickers, overlays=overlays, novelty=novelty, heat_score=heat_score)

        noise_flags = sorted({f for ov in overlays.values() for f in ov["noise"]["flags"]})
        scanner_overlaps = sorted({o for ov in overlays.values() for o in ov["scanner_overlaps"]})
        source_diversity = len(set(cluster["pipelines"]) | {"social_arb_confirmed"
                                                             for ov in overlays.values()
                                                             if ov["already_confirmed"]})

        thesis = (f"{len(mapped_tickers)} ticker(s) co-mentioned around "
                 f"\"{cluster['evidence_phrases'][0]}\" across {cluster['document_count']} "
                 f"snippet(s) from {len(cluster['source_domains'])} source(s)"
                 + (f"; overlaps known topic {cluster['known_topic']}." if cluster["known_topic"] else "."))

        price_confirmation = None
        if with_price_confirmation and cluster["known_topic"]:
            price_confirmation = price_confirmation_for_basket(cluster["known_topic"])

        alerts.append({
            "topic": name,
            "known_topic": cluster["known_topic"],
            "thesis": thesis,
            "evidence_phrases": cluster["evidence_phrases"],
            "source_domains": cluster["source_domains"],
            "source_diversity": source_diversity,
            "novelty_score": novelty["novelty_score"],
            "heat_score": heat_score,
            "acceleration_ratio": novelty["acceleration_ratio"],
            "history_maturity": novelty["history_maturity"],
            "is_recycled_topic": novelty["is_recycled_topic"],
            "mapped_tickers": mapped_tickers,
            "alpha_fit_overlays": {t: {"alpha_fit": ov["alpha_fit"], "noise": ov["noise"]}
                                   for t, ov in overlays.items()},
            "noise_risk_flags": noise_flags,
            "scanner_overlaps": scanner_overlaps,
            "price_confirmation": price_confirmation,
            "operator_label": label,
            "reason": reason,
            "research_only": True,
            "disclaimer": DISCLAIMER,
        })

    priority = {LABEL_RESEARCH_NOW: 0, LABEL_WATCH_FOR_RESET: 1, LABEL_CONFIRMATION_ONLY: 2,
               LABEL_OLD_RECHURN: 3, LABEL_EXHAUSTION_RISK: 4, LABEL_REDFLAG_NOISE: 5,
               LABEL_MAPPING_WEAK: 6}
    alerts.sort(key=lambda a: (priority.get(a["operator_label"], 9), -a["heat_score"]))

    counts: Dict[str, int] = {}
    for a in alerts:
        counts[a["operator_label"]] = counts.get(a["operator_label"], 0) + 1

    return {
        "kind": "topic_shock_detector",
        "version": VERSION,
        "generated_at": now.isoformat(),
        "asof_date": asof_date,
        "research_only": True,
        "promote_to_signal": False,
        "no_routing_change": True,
        "hot_does_not_mean_buy": True,
        "disclaimer": DISCLAIMER,
        "guardrails": _guardrails(),
        "source_dependencies": source_dependencies,
        "corpus": {
            "raw_snippets": len(snippets),
            "surviving_snippets": len(survivors),
            "suppressed_snippets": len(snippets) - len(survivors),
            "clusters_formed": len(clusters),
        },
        "suppressed_as_template": suppressed_as_template,
        "alerts": alerts,
        "counts": {"alerts": len(alerts), **counts},
        "_new_history_rows": new_history_rows,  # consumed by write_artifacts(); not public schema
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
        "no_program_verdict_change": True,
        "no_routing_change": True,
        "no_trade_recommendation": True,
        "no_evidence_ledger_mutation": True,
        "hot_does_not_mean_buy": True,
        "never_invents_ticker_mapping": True,
    }


# ── render ───────────────────────────────────────────────────────────────────
def render_txt(res: Dict[str, Any]) -> List[str]:
    if res.get("fallback"):
        return [f"== TOPIC SHOCK DETECTOR ({res['version']}) — {res['generated_at']} ==",
                f"fallback={res['fallback']} missing={res.get('missing_artifacts')}"]
    c = res["counts"]
    corp = res["corpus"]
    L = [
        f"== TOPIC SHOCK DETECTOR ({res['version']}) — {res['generated_at']} ==",
        res["disclaimer"],
        f"asof={res['asof_date']}  raw={corp['raw_snippets']} survived={corp['surviving_snippets']} "
        f"suppressed={corp['suppressed_snippets']} clusters={corp['clusters_formed']}",
        "counts: " + ", ".join(f"{k}={v}" for k, v in c.items() if k != "alerts") + f", alerts={c['alerts']}",
        "",
        "suppressed_as_template:",
    ]
    for s in res["suppressed_as_template"]:
        L.append(f"  {s['matched_pattern']:<32} docs={s['document_count']:<4} "
                 f"phrase={s['matched_phrase']!r} tickers={s['tickers'][:6]}")
    L += ["", f"{'topic':<28}{'label':<24}{'heat':>6}{'novelty':>9}{'tickers':>9}"]
    for a in res["alerts"][:40]:
        L.append(f"{a['topic']:<28}{a['operator_label']:<24}{a['heat_score']:>6.1f}"
                 f"{a['novelty_score']:>9.1f}{len(a['mapped_tickers']):>9}")
    return L


def render_md(res: Dict[str, Any]) -> List[str]:
    if res.get("fallback"):
        return [f"# Topic Shock Detector — {res['generated_at']}",
                "", f"**fallback:** `{res['fallback']}` (missing {res.get('missing_artifacts')})"]
    c = res["counts"]
    corp = res["corpus"]
    L = [
        f"# Topic Shock Detector ({res['version']}) — {res['asof_date']}",
        "",
        f"_{res['disclaimer']}_",
        "",
        f"**Corpus:** {corp['raw_snippets']} raw snippets, {corp['surviving_snippets']} survived "
        f"template suppression, {corp['suppressed_snippets']} suppressed, {corp['clusters_formed']} clusters formed.",
        "",
        "## Counts",
        "| label | count |", "|---|---|",
    ]
    for k, v in c.items():
        if k == "alerts":
            continue
        L.append(f"| {k} | {v} |")
    L += ["", "## Suppressed as template", "",
          "| pattern | matched phrase | docs | tickers |", "|---|---|---|---|"]
    for s in res["suppressed_as_template"]:
        L.append(f"| {s['matched_pattern']} | {s['matched_phrase']} | {s['document_count']} | "
                 f"{', '.join(s['tickers'][:8])} |")
    L += ["", "## Alerts", "",
          "| topic | label | heat | novelty | tickers | thesis |", "|---|---|---|---|---|---|"]
    for a in res["alerts"]:
        tickers = ", ".join(f"{t['ticker']}({t['confidence']:.2f})" for t in a["mapped_tickers"][:5])
        L.append(f"| {a['topic']} | {a['operator_label']} | {a['heat_score']:.1f} | "
                 f"{a['novelty_score']:.1f} | {tickers} | {a['thesis']} |")
    return L


def write_artifacts(res: Dict[str, Any], root: Optional[Path] = None) -> Dict[str, str]:
    root = Path(root) if root else REPO_ROOT
    new_rows = res.pop("_new_history_rows", [])
    json_path = root / OUT_JSON_REL
    md_path = root / OUT_MD_REL
    txt_path = root / OUT_TXT_REL
    ahr._write_json(json_path, res)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("\n".join(render_md(res)) + "\n", encoding="utf-8")
    txt_path.parent.mkdir(parents=True, exist_ok=True)
    txt_path.write_text("\n".join(render_txt(res)) + "\n", encoding="utf-8")
    if new_rows:
        ahr._append_jsonl(root / HISTORY_REL, new_rows)
    return {"json": str(json_path), "markdown": str(md_path), "text": str(txt_path)}


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Topic Shock Detector V0 — emergent topic-shock discovery (research-only, cache-only)")
    ap.add_argument("--root", default=None, help=argparse.SUPPRESS)
    ap.add_argument("--dry-run", action="store_true", help="print, write nothing")
    ap.add_argument("--print", action="store_true")
    ap.add_argument("--with-price-confirmation", action="store_true",
                    help="attach optional sympathy-basket price breadth (informational only)")
    args = ap.parse_args(argv)
    root = Path(args.root) if args.root else REPO_ROOT

    res = build(root, with_price_confirmation=args.with_price_confirmation)
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
