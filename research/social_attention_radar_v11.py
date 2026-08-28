#!/usr/bin/env python3
"""
research/social_attention_radar_v11.py — Social Attention v1.1 SHADOW MODE
(Phase 1G.15 repair plan, Step B).

RESEARCH-ONLY / SHADOW / PARALLEL.  This module NEVER writes to any artifact
the production pipeline reads (``social_attention_radar_latest.json``,
``social_attention_history.jsonl``, ``research_scanner.py``'s inputs,
``latest_scan_programs.py``, any program/gate/lens file). It reuses the
production radar's tested collection + velocity/lead-type machinery via
import (no duplication, no drift) and adds new, additive experiments on top:

  1. An EXPANDED watch universe (beyond the production 75/100 cap) built
     from the same cache-only buckets plus one new liquid-universe bucket.
  2. Source-health WEIGHTING: a reliability weight per source, seeded today
     and meant to sharpen as ``social_attention_v11_history.jsonl`` accrues
     (there is no per-row source breakdown in the production history file
     to backfill from, so this starts from an honest "insufficient
     history" state, not a fabricated prior).
  3. Source-DIVERSITY historization (the production history file only
     stores a normalized diversity *score*, not the raw source-type count
     per day — v1.1 records both going forward).
  4. A simple, explainable bot/spam/noise heuristic over raw StockTwits
     messages (per-ticker author concentration + near-duplicate text
     ratio) — informational only, never a filter.
  5. A RECALIBRATED crowd-stage classifier (``classify_stage_v11``) run
     ALONGSIDE the unmodified production classifier for direct comparison.
     Diagnosis: production's BROADENING_ATTENTION requires
     ``source_diversity >= 2``, which is structurally unreachable with
     Reddit disabled and Google Trends yielding ~0 spikes under the
     safe-nightly profile — that is why BROADENING_ATTENTION and
     VIRAL_CROWDING have had zero matured samples across 55 history days.
     The v1.1 variant substitutes a same-run percentile rank of
     attention_velocity_score for the diversity gate.
  6. A daily "Top Market Attention" list merging News Catalyst Radar +
     Social Attention Radar (production leads) + this module's own
     expanded-universe leads, each tagged with its origin so the two
     radars are never shown as one undifferentiated thing.

Doctrine (identical to the production radar): no paper signals, no trade
proposals, no execution/governance/gate/live-capital/production-universe/
DB changes. Social signal is never trade approval. Authors are one-way
hashed (reuses ``_author_hash``). No private-community scraping, no
API-ToS bypass, no PII. ``promote_to_signal`` is always false.

Outputs (all new, none overwrite an existing production artifact):
  cache/research/social_attention_v11_latest.json
  cache/research/social_attention_v11_watch_universe_latest.json
  cache/research/social_attention_v11_source_weights_latest.json
  cache/research/top_market_attention_latest.json
  logs/social_attention_v11_latest.txt
  data/research/social_attention_v11_history.jsonl   (append-only, idempotent/day)

Usage:
  GEM_TRADER_SKIP_DOTENV=true .venv/bin/python \\
      research/social_attention_radar_v11.py --offline-sample --dry-run
  SNIPER_ENV_PATH=/home/gem/secure/trading.env .venv/bin/python \\
      research/social_attention_radar_v11.py --enable-stocktwits
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.scanner_truth import dataio  # cache-only price + jsonl helpers
from research import social_attention_radar as base

VERSION = "SOCIAL_ATTENTION_V11_SHADOW"

DISCLAIMER = (
    "RESEARCH-ONLY SHADOW MODE. Parallel to, and never overwriting, "
    "social_attention_radar_latest.json. Nothing here emits signals, "
    "trades, or paper evidence; promote_to_signal is always false. "
    "Not read by research_scanner.py, latest_scan_programs.py, or any "
    "program/gate/lens file."
)

# ── paths (all new — never collide with the production module's files) ──────
OUT_JSON = dataio.RESEARCH_CACHE / "social_attention_v11_latest.json"
OUT_TXT = dataio.LOGS_DIR / "social_attention_v11_latest.txt"
WATCH_JSON = dataio.RESEARCH_CACHE / "social_attention_v11_watch_universe_latest.json"
WEIGHTS_JSON = dataio.RESEARCH_CACHE / "social_attention_v11_source_weights_latest.json"
TOP_ATTENTION_JSON = dataio.RESEARCH_CACHE / "top_market_attention_latest.json"
HISTORY = dataio.HISTORY_DIR / "social_attention_v11_history.jsonl"

# Expanded universe sizing.  Deliberately modest above the production 75/100
# cap — this is a manual/on-demand shadow run, not a scheduled cron job; a
# larger cap means proportionally more live StockTwits calls (same public,
# no-auth endpoint the production radar already uses).
EXPAND_CAP_DEFAULT = 150
EXPAND_HARD_MAX = 300

# Percentile rank (within this run's cohort) substituted for
# source_diversity>=2 in classify_stage_v11's BROADENING_ATTENTION gate.
BROADENING_PERCENTILE = 0.85

# Noise-score heuristic weights (0-100 composite; informational only).
NOISE_W_AUTHOR_CONCENTRATION = 0.5
NOISE_W_NEAR_DUPLICATE = 0.3
NOISE_W_TOP_AUTHOR_SHARE = 0.2

# Minimum matured v1.1 history rows before source-health weighting is
# treated as anything but a seed — mirrors the production forward
# validator's floor discipline (see social_attention_forward_validation.py).
MIN_HISTORY_ROWS_FOR_WEIGHTING = 200


# ── expanded watch universe (item 1) ─────────────────────────────────────────


def _additional_liquid_bucket(existing: Sequence[str], cap: int) -> List[str]:
    """A supplementary bucket beyond the production build_watch_universe()
    sources: names already surfaced on the current research scanner
    watchlist, cache-only, no provider calls. Additive — the production
    buckets (recall-shadow/rs-theme/alpha-board/lens-ready/manual/mega-cap)
    are used unchanged first; this only fills remaining cap."""
    out: List[str] = []
    seen = {t.upper() for t in existing}
    path = dataio.RESEARCH_CACHE / "research_scanner_latest.json"
    try:
        data = json.loads(path.read_text())
    except Exception:
        return out
    for row in data.get("watchlist") or []:
        if not isinstance(row, dict):
            continue
        t = str(row.get("ticker") or "").upper()
        if t and t not in seen and t not in base.COMMON_FALSE_TICKERS:
            seen.add(t)
            out.append(t)
        if len(out) >= cap:
            break
    return out


def build_expanded_watch_universe(cap: int = EXPAND_CAP_DEFAULT,
                                  hard_max: int = EXPAND_HARD_MAX) -> Dict[str, Any]:
    """Union of the production watch universe (unchanged buckets, called
    with a higher cap/hard_max — build_watch_universe() takes both as plain
    parameters, so this never edits the production module or its module-
    level WATCH_CAP_DEFAULT/WATCH_CAP_HARD_MAX constants) plus one new
    scanner-watchlist bucket to genuinely extend coverage."""
    cap = max(1, min(int(cap), int(hard_max)))
    prod = base.build_watch_universe(cap=cap, hard_max=hard_max)
    ordered = list(prod["universe"])
    extra = _additional_liquid_bucket(ordered, cap=max(0, cap - len(ordered)))
    ordered = ordered + extra
    source_counts = dict(prod["source_counts"])
    source_counts["scanner_watchlist_extra"] = len(extra)
    return {
        "kind": "social_attention_v11_watch_universe",
        "version": VERSION,
        "generated_at": base._utc_now().isoformat(),
        "cap": cap,
        "hard_max": hard_max,
        "size": len(ordered),
        "universe": ordered,
        "source_counts": source_counts,
        "production_watch_universe_size": prod["size"],
        "expansion_over_production": len(ordered) - prod["size"],
    }


# ── bot/spam/noise heuristic (item 4) ────────────────────────────────────────


def _normalize_text_for_dup(text: str) -> str:
    import re
    t = text.lower()
    t = re.sub(r"https?://\S+", "", t)
    t = re.sub(r"[^a-z0-9\s]", "", t)
    return re.sub(r"\s+", " ", t).strip()


def compute_noise_scores(items: Sequence["base.SocialItem"]) -> Dict[str, Dict[str, Any]]:
    """Per-ticker noise heuristic from StockTwits items only (the one
    high-volume, low-moderation source in this pipeline). Author hashes are
    already one-way hashed by normalize_items() — no raw PII is read here.
    Heuristic, not a filter: informational fields only, never used to drop
    a lead or change a score."""
    by_ticker: Dict[str, List["base.SocialItem"]] = {}
    for it in items:
        if it.source_type != "stocktwits":
            continue
        for t in it.ticker_candidates:
            by_ticker.setdefault(t, []).append(it)

    out: Dict[str, Dict[str, Any]] = {}
    for ticker, its in by_ticker.items():
        total = len(its)
        if total == 0:
            continue
        authors = [it.author_hash for it in its if it.author_hash]
        unique_authors = len(set(authors)) or total  # unknown authors: assume unique
        author_concentration = 1.0 - (unique_authors / total) if total else 0.0
        top_author_share = 0.0
        if authors:
            counts: Dict[str, int] = {}
            for a in authors:
                counts[a] = counts.get(a, 0) + 1
            top_author_share = max(counts.values()) / total
        norm_texts = [_normalize_text_for_dup(it.text) for it in its if it.text]
        dup_count = 0
        if norm_texts:
            seen: Dict[str, int] = {}
            for nt in norm_texts:
                if not nt:
                    continue
                seen[nt] = seen.get(nt, 0) + 1
            dup_count = sum(c - 1 for c in seen.values() if c > 1)
            near_duplicate_ratio = dup_count / len(norm_texts)
        else:
            near_duplicate_ratio = 0.0
        noise_score = round(min(100.0, 100.0 * (
            NOISE_W_AUTHOR_CONCENTRATION * author_concentration
            + NOISE_W_NEAR_DUPLICATE * near_duplicate_ratio
            + NOISE_W_TOP_AUTHOR_SHARE * top_author_share)), 1)
        out[ticker] = {
            "stocktwits_message_count": total,
            "unique_authors": unique_authors,
            "author_concentration": round(author_concentration, 3),
            "top_author_share": round(top_author_share, 3),
            "near_duplicate_ratio": round(near_duplicate_ratio, 3),
            "noise_score": noise_score,
        }
    return out


# ── recalibrated crowd-stage classifier (item 5) ─────────────────────────────


def classify_stage_v11(metrics: Dict[str, Any], price: Dict[str, Any],
                       options: Dict[str, Any],
                       velocity_percentile: float) -> str:
    """Experimental variant of base.classify_stage(). Identical for
    EXHAUSTION_RISK / VIRAL_CROWDING / STEALTH_ATTENTION (those gates don't
    depend on source_diversity); substitutes a same-run percentile rank of
    attention_velocity_score for the source_diversity>=2 BROADENING gate,
    since that gate is unreachable while Reddit stays disabled and Trends
    stays low-yield. NOT used in place of the production classifier
    anywhere — v1.1 output publishes both side by side; only a forward-
    validation gate (Step C) could justify preferring one over the other."""
    total = metrics.get("mention_count_24h", 0)
    accel = metrics.get("acceleration_ratio", 0.0) or 0.0
    meme = metrics.get("meme_hits", 0) > 0
    parabolic = bool(price.get("parabolic"))
    moving = bool(price.get("price_moving"))
    speculative_opts = bool(options.get("speculative"))

    if parabolic and (meme or speculative_opts):
        return "EXHAUSTION_RISK"
    if total >= base.VIRAL_MIN_MENTIONS and meme and moving:
        return "VIRAL_CROWDING"
    if total <= base.STEALTH_MAX_MENTIONS and accel >= base.HIGH_ACCEL:
        return "STEALTH_ATTENTION"
    if velocity_percentile >= BROADENING_PERCENTILE and (
            moving or metrics.get("attention_velocity_score", 0) >= 50):
        return "BROADENING_ATTENTION"
    if accel >= base.RISING_ACCEL and total < base.VIRAL_MIN_MENTIONS:
        return "EARLY_DISCOVERY"
    return "NO_SIGNAL"


def _percentile_ranks(values: Sequence[float]) -> List[float]:
    """Rank each value's percentile position (0-1) within the same list,
    ties sharing the lower rank. O(n log n); fine for a single run's
    cohort size (hundreds, not millions)."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    n = len(values)
    for pos, idx in enumerate(order):
        ranks[idx] = pos / (n - 1) if n > 1 else 1.0
    return ranks


# ── source-health weighting (item 2) ─────────────────────────────────────────


def compute_source_reliability_weights(
        history_v11: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Empirical reliability weight per dominant source_type: the rate at
    which that source's rows reached beyond NO_SIGNAL in v11 history.

    The production social_attention_history.jsonl never recorded which raw
    source_type(s) fed each row (only a normalized diversity *score*), so
    there is nothing to backfill from — this starts a NEW time series from
    today's v1.1 history file and is honestly reported as insufficient
    until MIN_HISTORY_ROWS_FOR_WEIGHTING rows accrue. Never used to filter
    or rescore a lead; informational context only."""
    n = len(history_v11)
    if n < MIN_HISTORY_ROWS_FOR_WEIGHTING:
        return {
            "status": "INSUFFICIENT_HISTORY",
            "rows_seen": n,
            "rows_required": MIN_HISTORY_ROWS_FOR_WEIGHTING,
            "weights": {},
            "note": ("Seeding a new time series today (2026-08-28) — the "
                     "production history file never recorded per-row "
                     "source_type, so there is nothing to backfill from. "
                     "Re-check once rows_seen >= rows_required."),
        }
    totals: Dict[str, int] = {}
    hits: Dict[str, int] = {}
    for row in history_v11:
        src = str(row.get("primary_source_type") or "unknown")
        totals[src] = totals.get(src, 0) + 1
        if str(row.get("crowd_stage") or "NO_SIGNAL") != "NO_SIGNAL":
            hits[src] = hits.get(src, 0) + 1
    weights = {
        src: round(hits.get(src, 0) / totals[src], 3)
        for src in totals if totals[src] > 0
    }
    return {
        "status": "COMPUTED",
        "rows_seen": n,
        "rows_required": MIN_HISTORY_ROWS_FOR_WEIGHTING,
        "weights": weights,
        "note": "weight = fraction of rows from that source reaching beyond NO_SIGNAL",
    }


def _primary_source_type(its: Sequence["base.SocialItem"]) -> str:
    counts: Dict[str, int] = {}
    for it in its:
        counts[it.source_type] = counts.get(it.source_type, 0) + 1
    return max(counts, key=counts.get) if counts else "unknown"


# ── build ─────────────────────────────────────────────────────────────────────


def build(args: argparse.Namespace) -> Dict[str, Any]:
    now = base._utc_now()
    stats = base._new_stats()

    watch = None
    if not args.offline_sample:
        watch = build_expanded_watch_universe(
            cap=args.expand_cap, hard_max=EXPAND_HARD_MAX)

    # Reuse the production collectors verbatim via a namespace shaped like
    # the production CLI's args (collect_raw/collect_stocktwits are
    # unmodified imports — no production file edited).
    base_args = argparse.Namespace(
        offline_sample=args.offline_sample,
        skip_manual=args.skip_manual,
        skip_google_trends=args.skip_google_trends,
        google_trends_z=1.5, google_trends_cap=40,
        enable_stocktwits=args.enable_stocktwits,
        stocktwits_cap=(watch["size"] if watch else 0) or args.expand_cap,
        enable_reddit=False,
    )
    raw = base.collect_raw(base_args, stats,
                          watch_universe=(watch or {}).get("universe"))
    items = base.normalize_items(raw)
    history = dataio.read_jsonl(base.HISTORY)  # production history: same
                                               # baseline z-score reused for
                                               # apples-to-apples velocity
    velocity = base.compute_velocity(items, history, now=now)
    news_map = base._news_first_seen()
    noise = compute_noise_scores(items)

    by_ticker_items: Dict[str, List["base.SocialItem"]] = {}
    for it in items:
        for t in it.ticker_candidates:
            by_ticker_items.setdefault(t, []).append(it)

    vel_scores = [m.get("attention_velocity_score", 0.0) for m in velocity.values()]
    percentiles = dict(zip(velocity.keys(), _percentile_ranks(vel_scores)))

    leads: List[Dict[str, Any]] = []
    for ticker, m in velocity.items():
        price = base._price_overlay(ticker)
        options = base._options_overlay(ticker)
        prod_stage = base.classify_stage(m, price, options)
        v11_stage = classify_stage_v11(m, price, options, percentiles.get(ticker, 0.0))
        first_seen = base._parse_dt(m["first_seen_at"]) or now
        lead_type, lead_time = base.classify_lead_type(ticker, first_seen, news_map)
        label = base.assign_label(prod_stage, lead_type, m, price)
        leads.append({
            **m,
            "crowd_stage": prod_stage,
            "crowd_stage_v11": v11_stage,
            "stage_changed_v11": prod_stage != v11_stage,
            "lead_type": lead_type,
            "lead_time_hours": lead_time,
            "label": label,
            "velocity_percentile": round(percentiles.get(ticker, 0.0), 3),
            "primary_source_type": _primary_source_type(by_ticker_items.get(ticker, [])),
            "noise": noise.get(ticker),
            "price_overlay": price,
            "options_overlay": options,
        })

    leads.sort(key=lambda x: -(x.get("attention_velocity_score") or 0))

    stage_shift_count = sum(1 for l in leads if l["stage_changed_v11"])
    counts = {
        "leads": len(leads),
        "prod_broadening": sum(1 for l in leads if l["crowd_stage"] == "BROADENING_ATTENTION"),
        "v11_broadening": sum(1 for l in leads if l["crowd_stage_v11"] == "BROADENING_ATTENTION"),
        "prod_viral": sum(1 for l in leads if l["crowd_stage"] == "VIRAL_CROWDING"),
        "v11_viral": sum(1 for l in leads if l["crowd_stage_v11"] == "VIRAL_CROWDING"),
        "stage_disagreements": stage_shift_count,
        "high_noise_count": sum(1 for l in leads
                                if (l.get("noise") or {}).get("noise_score", 0) >= 60),
    }

    history_v11 = dataio.read_jsonl(HISTORY)
    weights = compute_source_reliability_weights(history_v11)

    return {
        "kind": "social_attention_v11_shadow",
        "version": VERSION,
        "research_only": True,
        "promote_to_signal": False,
        "generated_at": now.isoformat(),
        "disclaimer": DISCLAIMER,
        "asof_date": now.date().isoformat(),
        "watch_universe": ({"size": watch["size"], "cap": watch["cap"],
                            "source_counts": watch["source_counts"],
                            "expansion_over_production":
                                watch["expansion_over_production"]}
                           if watch else None),
        "n_raw_items": len(raw),
        "n_normalized_items": len(items),
        "n_tickers": len(velocity),
        "counts": counts,
        "source_reliability_weights": weights,
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
            "crowd_stage": l["crowd_stage"],
            "crowd_stage_v11": l["crowd_stage_v11"],
            "stage_changed_v11": l["stage_changed_v11"],
            "lead_type": l["lead_type"],
            "primary_source_type": l["primary_source_type"],
            "velocity_percentile": l["velocity_percentile"],
            "noise_score": (l.get("noise") or {}).get("noise_score"),
            "metrics": {
                "mention_count_24h": l["mention_count_24h"],
                "attention_velocity_score": l["attention_velocity_score"],
                "source_diversity_score": l["source_diversity_score"],
            },
        })
    return rows


def _existing_asof_dates(path: Path) -> set:
    return {str(r.get("asof_date")) for r in dataio.read_jsonl(path)}


# ── Top Market Attention list (item 6) ───────────────────────────────────────


def build_top_market_attention(v11_res: Dict[str, Any],
                               cap: int = 40) -> Dict[str, Any]:
    """Daily combined ranking: News Catalyst Radar items + this run's v1.1
    leads, each explicitly tagged by origin so the two radars are never
    rendered as one undifferentiated thing (the exact conflation Step A
    fixed in the production display layer — this is the equivalent fix at
    the shadow-module's own output)."""
    now = base._utc_now()
    rows: List[Dict[str, Any]] = []

    for l in v11_res.get("leads", []):
        if l.get("label") == "NO_SOCIAL_EDGE":
            continue
        rows.append({
            "ticker": l["ticker"],
            "origin": "SOCIAL_ATTENTION_V11",
            "score": l.get("attention_velocity_score"),
            "crowd_stage": l["crowd_stage"],
            "crowd_stage_v11": l["crowd_stage_v11"],
            "lead_type": l["lead_type"],
            "noise_score": (l.get("noise") or {}).get("noise_score"),
        })

    nc_path = dataio.RESEARCH_CACHE / "social_arb_latest.json"
    try:
        nc_data = json.loads(nc_path.read_text())
        v11_tickers = {r["ticker"] for r in rows}
        for item in nc_data.get("items") or []:
            t = str(item.get("ticker") or "").upper()
            if not t:
                continue
            existing = next((r for r in rows if r["ticker"] == t), None)
            if existing:
                existing["origin"] = "BOTH"
                existing["news_catalyst_score"] = item.get("deterministic_score")
            else:
                rows.append({
                    "ticker": t,
                    "origin": "NEWS_CATALYST",
                    "score": item.get("deterministic_score"),
                    "crowd_stage": None,
                    "crowd_stage_v11": None,
                    "lead_type": None,
                    "noise_score": None,
                    "news_catalyst_score": item.get("deterministic_score"),
                })
    except Exception:
        pass

    rows.sort(key=lambda r: -(r.get("score") or 0))
    return {
        "kind": "top_market_attention",
        "version": VERSION,
        "research_only": True,
        "promote_to_signal": False,
        "generated_at": now.isoformat(),
        "disclaimer": DISCLAIMER,
        "rows": rows[:cap],
        "total_candidates": len(rows),
    }


# ── render ───────────────────────────────────────────────────────────────────


def render_txt(res: Dict[str, Any]) -> List[str]:
    L = [
        f"== SOCIAL ATTENTION v1.1 SHADOW ({res['version']}) — {res['generated_at']} ==",
        res["disclaimer"],
        f"watch_universe: {res.get('watch_universe')}",
        f"n_raw_items={res['n_raw_items']}  n_tickers={res['n_tickers']}",
        f"counts: {res['counts']}",
        f"source_reliability_weights: {res['source_reliability_weights']}",
        "",
        f"{'ticker':<8}{'vel':>6}{'pctl':>6}  {'prod_stage':<20}{'v11_stage':<20}"
        f"{'lead_type':<12}{'noise':>7}",
    ]
    for l in res["leads"][:40]:
        noise = (l.get("noise") or {}).get("noise_score")
        L.append(
            f"{l['ticker']:<8}{l.get('attention_velocity_score', 0):>6.1f}"
            f"{l.get('velocity_percentile', 0):>6.2f}  "
            f"{l['crowd_stage']:<20}{l['crowd_stage_v11']:<20}"
            f"{l['lead_type']:<12}{(noise if noise is not None else 0):>7.1f}")
    return L


# ── CLI ───────────────────────────────────────────────────────────────────────


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Social Attention v1.1 shadow mode (research-only, "
                    "parallel to the production radar)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print only; write nothing")
    ap.add_argument("--offline-sample", action="store_true",
                    help="use the production module's offline fixture, no network")
    ap.add_argument("--skip-manual", action="store_true")
    ap.add_argument("--skip-google-trends", action="store_true")
    ap.add_argument("--enable-stocktwits", action="store_true", default=True,
                    help="on by default (matches safe-nightly); pass "
                        "--no-stocktwits to disable")
    ap.add_argument("--no-stocktwits", dest="enable_stocktwits",
                    action="store_false")
    ap.add_argument("--expand-cap", type=int, default=EXPAND_CAP_DEFAULT,
                    help=f"expanded watch-universe size (default "
                        f"{EXPAND_CAP_DEFAULT}, hard max {EXPAND_HARD_MAX})")
    return ap.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    res = build(args)
    top = build_top_market_attention(res)

    for line in render_txt(res):
        print(line)

    if args.dry_run:
        print("\n[dry-run] nothing written.")
        return 0

    dataio.write_json(OUT_JSON, res)
    dataio.write_text(OUT_TXT, render_txt(res))
    if res.get("watch_universe"):
        dataio.write_json(WATCH_JSON, {
            **res["watch_universe"], "kind": "social_attention_v11_watch_universe",
            "version": VERSION, "generated_at": res["generated_at"]})
    dataio.write_json(WEIGHTS_JSON, {
        **res["source_reliability_weights"],
        "kind": "social_attention_v11_source_weights",
        "version": VERSION, "generated_at": res["generated_at"]})
    dataio.write_json(TOP_ATTENTION_JSON, top)

    existing_dates = _existing_asof_dates(HISTORY)
    if res["asof_date"] not in existing_dates:
        n = dataio.append_jsonl(HISTORY, _history_rows(res))
        print(f"appended {n} history row(s) for {res['asof_date']}")
    else:
        print(f"history already has {res['asof_date']} — skip (idempotent)")

    print(f"\nwrote {dataio.rel_to_repo(OUT_JSON)} · "
         f"{dataio.rel_to_repo(TOP_ATTENTION_JSON)} · "
         f"{res['counts']['leads']} leads, "
         f"{res['counts']['stage_disagreements']} stage disagreements "
         f"vs production classifier")
    return 0


if __name__ == "__main__":
    sys.exit(main())
