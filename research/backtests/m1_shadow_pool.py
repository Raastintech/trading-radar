"""M1 shadow source pool — a wide, unordered research pool, gated on data.

RESEARCH ONLY. The first report built on the M1 data foundation
(``m1_data_guard`` + ``m1_price_refresh``). It publishes what the M1 decision
memo says the evidence supports and nothing more:

    a WIDE, EQUAL-WEIGHT, UNORDERED pool of names that currently sit in the
    12-1 momentum quintile, annotated with facts a human should check.

What it deliberately does not do, because four studies say the evidence does
not support it:

* **no ranking.** Names are listed alphabetically. Position carries no
  information and is not a claim. The per-name momentum value is not even
  emitted — publishing it would invite a re-sort, and a re-sort is a ranking.
* **no conviction tiers, no top-10, no top-25, no "best".** M1's edge is a
  basket property; its ticker-clustered interval is negative at every horizon,
  which means the basket beats the tape while the typical name in it does not.
* **no scores.** Tags are annotations. None of them is combined into a number.
* **no trade signals.** No entry, stop, target, size or holding period.
* **no live verdict.** The live evidence ladder cannot be emitted from here;
  its tokens are refused by :func:`assert_clean_language` before any write.

Membership is the only claim. Inside the pool, one name is not a better idea
than another, and this report says so in every artifact it writes.

Gating, enforced before anything is built:

* :func:`require_m1_data_ready` runs first. If the guard refuses, this module
  writes NOTHING and exits non-zero with the refusal reasons.
* The guard's data-integrity header is attached to every artifact.
* Zero provider calls — the module is cache-only and imports under
  ``offline_env()``.

Writes ONLY:
    cache/research/m1_shadow_pool_latest.json
    cache/research/m1_shadow_pool_forward_rows.jsonl
    logs/m1_shadow_pool_latest.txt
    docs/research/M1_SHADOW_SOURCE_POOL_REPORT.md

Usage::

    GEM_TRADER_SKIP_DOTENV=true .venv/bin/python -m research.backtests.m1_shadow_pool
"""

from __future__ import annotations

import argparse
import json
import math
import re
import time
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from research.backtests.common import (
    LiveArtifactTripwire,
    ROOT,
    assert_provenance,
    assert_replay_write_path,
    offline_env,
    provenance_flags,
    run_cli,
    write_replay_json,
    write_replay_text,
)
from research.backtests.m1_data_guard import (
    M1DataAudit,
    M1DataRefusal,
    M1_MIN_BARS_REQUIRED,
    PRICES_REL,
    TickerDepth,
    _required_session,
    evaluate,
    load_quarantine_excludes,
    load_refresh_outcome,
    require_m1_data_ready,
    scan_cache,
)

offline_env()

LATEST_REL = "cache/research/m1_shadow_pool_latest.json"
FORWARD_ROWS_REL = "cache/research/m1_shadow_pool_forward_rows.jsonl"
REPORT_TXT_REL = "logs/m1_shadow_pool_latest.txt"
DOC_REL = "docs/research/M1_SHADOW_SOURCE_POOL_REPORT.md"

#: Pool size band from the brief. Below the floor the report refuses rather
#: than publishing a thin list that would read as a shortlist.
POOL_MIN = 50
POOL_MAX = 100
POOL_DEFAULT = 75

#: The M1 definition: the within-date top quintile of 12-1 momentum. The cut is
#: how MEMBERSHIP is decided; it is not an ordering anyone may read.
M1_QUINTILE = 0.20

#: Trailing window for the analyst-attention annotation, in sessions.
ATTENTION_WINDOW = 63

#: An earnings row older than this is reported as stale rather than as a beat
#: or a miss — the cached calendar is a historical study artifact, not a live
#: feed, and a six-month-old "beat" tag would read as current news.
EARNINGS_STALE_DAYS = 120

#: Sources this report annotates from. Every one is read-only.
ANALYST_FEED_REL = "cache/research/m1_analyst_actions_intermediates/grades_raw.jsonl"
EARNINGS_FEED_REL = "cache/research/m1_surprise_intermediates/earnings_calendar_raw.jsonl"
FUNDAMENTALS_REL = "cache/replay_fundamentals"
OVERLAP_ARTIFACTS: dict[str, str] = {
    "high_conviction": "cache/research/high_conviction_alpha_latest.json",
    "emerging_outlier": "cache/research/emerging_outlier_watch_latest.json",
    "alpha_focus": "cache/research/alpha_focus_latest.json",
    "topic_shock": "cache/research/topic_shock_latest.json",
    "social_attention": "cache/research/social_attention_radar_latest.json",
    "research_scanner": "cache/research/research_scanner_latest.json",
}

FORWARD_HORIZONS: tuple[int, ...] = (20, 45, 60, 90)

NOTE = (
    "M1 SHADOW SOURCE POOL — RESEARCH ONLY. A wide, equal-weight, UNORDERED "
    "research pool, not a shortlist, not a ranking, not a conviction tier and "
    "not a trade signal. Membership is the only claim this report makes: "
    "inside the pool no name is a better idea than another, and position in "
    "any list here carries no information. NOT live forward evidence, NOT "
    "backtest evidence for Phase 4B, and NOT a gate/threshold/score/routing "
    "recommendation. Must never be pooled with the live forward ledger, "
    "program verdicts, HC/EO routing, Alpha Focus, MCP or the dashboard."
)

#: Language this report must never contain. Checked against every rendered
#: artifact before it is written, so a future edit cannot quietly reintroduce
#: shortlist framing.
FORBIDDEN_PHRASES: tuple[str, ...] = (
    "validated_edge", "alpha pick", "alpha picks", "top pick", "best idea",
    "conviction tier", "highest conviction", "buy candidate", "trade signal",
    "entry price", "price target", "stop loss", "position size",
    "top 10 names", "top 25 names", "ranked list", "our best",
    "strongest name", "highest ranked",
)


#: Tokens that may not appear at all, negated or otherwise.
ABSOLUTE_FORBIDDEN: tuple[str, ...] = ("validated_edge",)

#: A report has to be able to say what it is NOT. "no conviction tiers" is the
#: opposite of a conviction tier, and a checker that cannot tell them apart
#: would force the disclaimers out of the document — the exact wrong outcome.
NEGATION_CUES: tuple[str, ...] = (
    "no ", "not ", "never", "cannot", "can't", "refus", "forbid", "without",
    "does not", "do not", "must not", "isn't", "nor ", "neither", "avoid",
    "instead of", "rather than", "would be",
)
NEGATION_WINDOW = 60


class ForbiddenLanguage(RuntimeError):
    """Rendered output contained shortlist or trade-signal framing."""


def assert_clean_language(text: str, *, where: str) -> str:
    """Refuse to publish text that reads as a recommendation.

    Un-negated occurrences only: the phrase has to be a claim, not a
    disclaimer. Absolute tokens are refused either way.
    """
    low = text.lower()
    bad: list[str] = []
    for token in ABSOLUTE_FORBIDDEN:
        if token in low:
            bad.append(f"{token!r} (never permitted)")
    for phrase in FORBIDDEN_PHRASES:
        if phrase in ABSOLUTE_FORBIDDEN:
            continue
        start = 0
        while True:
            i = low.find(phrase, start)
            if i == -1:
                break
            context = low[max(0, i - NEGATION_WINDOW):i]
            if not any(cue in context for cue in NEGATION_CUES):
                bad.append(f"{phrase!r} in: ...{low[max(0, i - 40):i + len(phrase) + 12]}...")
            start = i + len(phrase)
    if bad:
        raise ForbiddenLanguage(f"{where} contains forbidden framing: {bad}")
    return text


# ── membership ──────────────────────────────────────────────────────────────


def m1_membership(depths: Sequence[TickerDepth], required_session: date
                  ) -> tuple[list[TickerDepth], dict[str, Any]]:
    """The current 12-1 quintile, and how the cut was made.

    Membership requires a cut, and a cut requires a comparison — that is the
    factor's own definition and it is unavoidable. What is avoidable, and
    avoided, is letting the comparison leak out as an ordering: the returned
    members are alphabetical and their momentum values are not published.
    """
    eligible = [
        d for d in depths
        if d.readable and not d.contaminated and d.passes_liquidity_floor
        and d.is_active(required_session) and not d.is_stale(required_session)
        and d.has_m1
    ]
    if not eligible:
        return [], {"eligible": 0, "cut_at_pct": None, "members": 0}
    ranked = sorted(eligible, key=lambda d: d.mom_12_1, reverse=True)
    n_take = max(1, int(round(len(ranked) * M1_QUINTILE)))
    members = ranked[:n_take]
    cut_value = members[-1].mom_12_1 if members else None
    moms = [d.mom_12_1 for d in members]
    meta = {
        "eligible_names": len(eligible),
        "quintile": M1_QUINTILE,
        "members": len(members),
        "cut_rule": "within-session top quintile of mom_12_1 among eligible names",
        "cut_value_pct": round(float(cut_value), 2) if cut_value is not None else None,
        # Distribution only. Per-name values are deliberately not published.
        "membership_momentum_distribution_pct": {
            "min": round(float(min(moms)), 2), "max": round(float(max(moms)), 2),
            "median": round(float(sorted(moms)[len(moms) // 2]), 2),
        },
        "ordering_note": (
            "The cut decides MEMBERSHIP. It is not a ranking, it is not "
            "published per name, and no member is a better idea than another."),
    }
    return sorted(members, key=lambda d: d.ticker), meta


def draw_pool(members: Sequence[TickerDepth], *, size: int, session: date,
              seed: int | None = None) -> tuple[list[TickerDepth], dict[str, Any]]:
    """Draw the published pool from membership WITHOUT ranking it.

    A uniform draw is the only selection that does not smuggle in a claim:
    taking "the top N" would be exactly the ordering the evidence contradicts.
    The draw is seeded off the session date, so the same session always
    produces the same pool and nobody can re-roll it until they like the names.
    """
    import numpy as np

    n = max(0, min(int(size), len(members)))
    s = seed if seed is not None else int(session.strftime("%Y%m%d"))
    rng = np.random.default_rng(s)
    idx = rng.choice(len(members), size=n, replace=False) if members else []
    drawn = sorted((members[int(i)] for i in idx), key=lambda d: d.ticker)
    meta = {
        "membership_size": len(members),
        "published_pool_size": len(drawn),
        "selection": "uniform random draw without replacement from membership",
        "seed": s,
        "seed_rule": "session date (YYYYMMDD) unless --seed overrides",
        "why_random": (
            "Taking the 'top N' of the membership would be a ranking, and the "
            "ticker-clustered evidence says position inside M1 carries no "
            "information. A seeded uniform draw publishes a readable slice "
            "without making a claim the evidence does not support."),
        "display_order": "alphabetical — carries no information",
    }
    return drawn, meta


# ── annotation sources (all read-only) ──────────────────────────────────────


def _read_jsonl(path: Path, limit: int | None = None) -> list[dict]:
    if not path.exists():
        return []
    out = []
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
            if limit and len(out) >= limit:
                break
    return out


def analyst_attention(root: Path, tickers: set[str], asof: date) -> dict[str, dict]:
    """Rating-change annotation per name, from the committed grades feed.

    The analyst-action study's finding is carried with the tag rather than left
    for the reader to misremember: the feed marks ATTENTION, not direction —
    upgrades and downgrades both sat above the untouched cohort — so a rating
    change is a prompt to look, never a reason to prefer one member.
    """
    from research.backtests.m1_analyst_actions import classify_action, grade_ordinal

    rows = _read_jsonl(root / ANALYST_FEED_REL)
    if not rows:
        return {}
    window_start = asof - timedelta(days=int(ATTENTION_WINDOW * 1.45))
    out: dict[str, dict] = {}
    for r in rows:
        sym = str(r.get("symbol") or "").upper()
        if sym not in tickers:
            continue
        d = str(r.get("date") or "")[:10]
        if not d:
            continue
        try:
            when = date.fromisoformat(d)
        except ValueError:
            continue
        node = out.setdefault(sym, {"up": 0, "down": 0, "maintain": 0, "other": 0,
                                    "firms": set(), "last_action": None})
        if node["last_action"] is None or d > node["last_action"]:
            node["last_action"] = d
        # Usable only strictly after the event date; an action dated today is
        # not yet actionable information, the same rule the study enforced.
        if when >= asof or when < window_start:
            continue
        klass = classify_action(grade_ordinal(r.get("prev")),
                                grade_ordinal(r.get("new")), r.get("action"))
        if klass == "upgrade":
            node["up"] += 1
            node["firms"].add(str(r.get("firm") or "?"))
        elif klass == "downgrade":
            node["down"] += 1
        elif klass == "maintain":
            node["maintain"] += 1
        else:
            node["other"] += 1
    tags: dict[str, dict] = {}
    for sym, n in out.items():
        up, down, maint = n["up"], n["down"], n["maintain"]
        if up and down:
            tag = "rating_changes_both_ways"
        elif up:
            tag = "rating_raised"
        elif down:
            tag = "rating_cut"
        elif maint:
            tag = "covered_no_change"
        else:
            tag = "no_action_in_window"
        tags[sym] = {
            "tag": tag, "upgrades": up, "downgrades": down, "maintains": maint,
            "distinct_upgrading_firms": len(n["firms"]),
            "last_action_date": n["last_action"],
            "window_sessions": ATTENTION_WINDOW,
            "reading": ("attention, not direction — the study found upgrades "
                        "(+0.73pp) and downgrades (+0.50pp) both above the "
                        "untouched cohort; this is a prompt to look, not a "
                        "reason to prefer this name"),
        }
    return tags


def earnings_feed_asof(root: Path) -> str | None:
    """Newest report date in the cached calendar — the feed's own horizon."""
    newest = None
    for r in _read_jsonl(root / EARNINGS_FEED_REL):
        d = str(r.get("date") or "")[:10]
        if d and (newest is None or d > newest):
            newest = d
    return newest


def earnings_surprise(root: Path, tickers: set[str], asof: date) -> dict[str, dict]:
    """Last reported EPS/revenue surprise per name, with its age stated."""
    rows = _read_jsonl(root / EARNINGS_FEED_REL)
    if not rows:
        return {}
    latest: dict[str, dict] = {}
    for r in rows:
        sym = str(r.get("symbol") or "").upper()
        if sym not in tickers:
            continue
        d = str(r.get("date") or "")[:10]
        if not d or d >= asof.isoformat():
            continue
        cur = latest.get(sym)
        if cur is None or d > cur["date"]:
            latest[sym] = {"date": d, "row": r}
    out: dict[str, dict] = {}
    for sym, node in latest.items():
        r = node["row"]
        age = (asof - date.fromisoformat(node["date"])).days
        ea, ee = _num(r.get("epsActual")), _num(r.get("epsEstimated"))
        ra, re_ = _num(r.get("revenueActual")), _num(r.get("revenueEstimated"))
        if age > EARNINGS_STALE_DAYS or ea is None or ee is None:
            tag = "no_recent_report_in_cached_feed"
        elif ea > ee and (ra is None or re_ is None or ra > re_):
            tag = "beat"
        elif ea > ee:
            tag = "eps_beat_revenue_miss"
        elif ea < ee:
            tag = "miss"
        else:
            tag = "in_line"
        out[sym] = {
            "tag": tag, "report_date": node["date"], "age_days": age,
            "eps_actual": ea, "eps_estimated": ee,
            "feed_as_of": "cached earnings calendar from the surprise study",
            "caveat": ("the cached calendar is a historical study artifact, not "
                       "a live feed; anything older than "
                       f"{EARNINGS_STALE_DAYS} days is reported as stale rather "
                       "than as news"),
        }
    return out


def _num(v):
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def fundamental_quality(root: Path, tickers: Iterable[str], asof: date
                        ) -> dict[str, dict]:
    """Profitable / cash-generative / non-diluting, as of the last filing."""
    from research.backtests.alpha_reconstruction_lab import _fund_features, _usable_date

    out: dict[str, dict] = {}
    src = root / FUNDAMENTALS_REL
    for sym in tickers:
        f = src / f"{sym}.json"
        if not f.exists():
            out[sym] = {"tag": "no_fundamental_data"}
            continue
        try:
            raw = json.loads(f.read_text())
        except Exception:
            out[sym] = {"tag": "unreadable_fundamentals"}
            continue
        merged: dict[str, dict] = {}
        for kind in ("income", "balance", "cashflow"):
            for r in (raw.get(kind) or []):
                d = str(r.get("date") or "")
                if not d or not r.get("acceptedDate"):
                    continue
                node = merged.setdefault(d, {"date": d, "_usable": None})
                node.update({k: v for k, v in r.items() if k != "date"})
                u = _usable_date(r["acceptedDate"])
                if u is not None and (node["_usable"] is None or u > node["_usable"]):
                    node["_usable"] = u
        known = sorted((v for v in merged.values()
                        if v["_usable"] and v["_usable"] <= asof),
                       key=lambda r: r["date"])
        if len(known) < 4:
            out[sym] = {"tag": "insufficient_filings"}
            continue
        feats = _fund_features(list(reversed(known[-9:])))
        prof, fcf = feats.get("profitable"), feats.get("fcf_positive")
        dil = feats.get("dilution_yoy_pct")
        if prof is None or fcf is None:
            tag = "incomplete"
        elif prof == 1.0 and fcf == 1.0 and (dil is None or dil < 5):
            tag = "quality_pass"
        else:
            tag = "quality_fail"
        out[sym] = {
            "tag": tag,
            "profitable": None if prof is None else bool(prof),
            "fcf_positive": None if fcf is None else bool(fcf),
            "dilution_yoy_pct": None if dil is None else round(float(dil), 1),
            "rev_yoy_pct": (None if feats.get("rev_yoy_pct") is None
                            else round(float(feats["rev_yoy_pct"]), 1)),
            "last_filing_used": known[-1]["date"],
            "quarters_available": feats.get("quarters_available"),
            "definition": "profitable AND fcf-positive AND dilution < 5% YoY",
        }
    return out


# ── price-derived annotations ───────────────────────────────────────────────

TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,11}$")


def price_annotations(root: Path, tickers: Sequence[str]) -> dict[str, dict]:
    """Liquidity, volatility and exhaustion facts, read from the deep cache.

    Flags, never a score. Each one is a reason for a human to look at
    something specific; none of them is combined into a number, and nothing
    here orders the pool.
    """
    import numpy as np
    import pandas as pd

    out: dict[str, dict] = {}
    for sym in tickers:
        f = root / PRICES_REL / f"{sym}.parquet"
        if not f.exists():
            out[sym] = {"data_quality": ["no_series"]}
            continue
        try:
            df = pd.read_parquet(f).sort_index()
        except Exception:
            out[sym] = {"data_quality": ["unreadable_series"]}
            continue
        close = np.asarray(df["close"], dtype=float)
        n = close.size
        high = np.asarray(df["high"], dtype=float) if "high" in df else close
        low = np.asarray(df["low"], dtype=float) if "low" in df else close
        vol = np.asarray(df["volume"], dtype=float) if "volume" in df else np.zeros(n)

        price = float(close[-1])
        w = min(20, n)
        dv = (close[-w:] * vol[-w:])
        dv = dv[np.isfinite(dv)]
        dvol_med = float(np.median(dv)) if dv.size else None
        dvol_mean = float(np.mean(dv)) if dv.size else None
        fake_ratio = (dvol_mean / dvol_med) if (dvol_med and dvol_med > 0) else None

        tr = np.maximum(high[1:] - low[1:],
                        np.maximum(np.abs(high[1:] - close[:-1]),
                                   np.abs(low[1:] - close[:-1])))
        atr = float(np.mean(tr[-14:])) if tr.size >= 14 else None
        atr_pct = (atr / price * 100.0) if (atr and price) else None

        ma50 = float(np.mean(close[-50:])) if n >= 50 else None
        ma200 = float(np.mean(close[-200:])) if n >= 200 else None
        look = close[-252:] if n >= 252 else close
        hi252 = float(np.max(look))
        pos = int(np.argmax(look))
        days_since_high = int(look.size - 1 - pos)

        flags: list[str] = []
        dist50 = ((price / ma50 - 1.0) * 100.0) if ma50 else None
        dist200 = ((price / ma200 - 1.0) * 100.0) if ma200 else None
        below_high = (price / hi252 - 1.0) * 100.0 if hi252 else None
        if dist50 is not None and dist50 >= 25:
            flags.append("extended_far_above_ma50")
        if atr_pct is not None and atr_pct >= 8:
            flags.append("high_daily_range")
        if below_high is not None and below_high >= -3:
            flags.append("at_or_near_52w_high")
        if below_high is not None and below_high <= -40:
            flags.append("deep_drawdown_from_52w_high")
        if dist200 is not None and dist200 < 0:
            flags.append("below_ma200")
        if fake_ratio is not None and fake_ratio >= 4.0:
            flags.append("block_traded_liquidity")

        dq: list[str] = []
        if n < M1_MIN_BARS_REQUIRED:
            dq.append(f"only_{n}_bars")
        if price < 5:
            dq.append("low_price")
        if dvol_med is not None and dvol_med < 5e6:
            dq.append("thin_dollar_volume")

        out[sym] = {
            "price": round(price, 2),
            "dollar_volume_median_20d": (None if dvol_med is None
                                         else round(dvol_med / 1e6, 2)),
            "liquidity_band": _liquidity_band(dvol_med),
            "fake_liquidity_ratio": (None if fake_ratio is None
                                     else round(fake_ratio, 2)),
            "atr14_pct": None if atr_pct is None else round(atr_pct, 2),
            "pct_vs_ma50": None if dist50 is None else round(dist50, 1),
            "pct_vs_ma200": None if dist200 is None else round(dist200, 1),
            "pct_below_52w_high": None if below_high is None else round(below_high, 1),
            "sessions_since_52w_high": days_since_high,
            "risk_flags": flags,
            "data_quality": dq,
            "bars": n,
            "last_bar": str(pd.DatetimeIndex(df.index)[-1].date()),
        }
    return out


def _liquidity_band(dvol: float | None) -> str:
    if dvol is None:
        return "unknown"
    if dvol >= 1e8:
        return "very_liquid_100M_plus"
    if dvol >= 2e7:
        return "liquid_20M_100M"
    if dvol >= 5e6:
        return "moderate_5M_20M"
    return "thin_under_5M"


# ── live-artifact overlap (read-only) ───────────────────────────────────────


def _tickers_in(obj: Any, found: set[str], depth: int = 0) -> None:
    """Collect ticker-shaped values under ticker/symbol keys, at any depth."""
    if depth > 8:
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str) and str(k).lower() in ("ticker", "symbol"):
                s = v.strip().upper()
                if TICKER_RE.match(s):
                    found.add(s)
            else:
                _tickers_in(v, found, depth + 1)
    elif isinstance(obj, list):
        for v in obj[:5000]:
            _tickers_in(v, found, depth + 1)


def artifact_tickers(root: Path, rel: str) -> tuple[set[str], str]:
    """Every ticker named in a live artifact. Read-only; never writes."""
    p = root / rel
    if not p.exists():
        return set(), "absent"
    try:
        data = json.loads(p.read_text())
    except Exception:
        return set(), "unreadable"
    found: set[str] = set()
    _tickers_in(data, found)
    return found, "read"


def saturation_warnings(root: Path, tickers: set[str]) -> dict[str, dict]:
    """Names sitting in the old scanner's score=100 saturation zone.

    One of the two cohorts the decision memo found reliably NEGATIVE across
    train and holdout. Carried as a warning on a member, never as a filter:
    this report does not remove names, it annotates them.
    """
    p = root / OVERLAP_ARTIFACTS["research_scanner"]
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text())
    except Exception:
        return {}
    hits: dict[str, dict] = {}

    def walk(obj: Any, depth: int = 0) -> None:
        if depth > 8:
            return
        if isinstance(obj, dict):
            sym = obj.get("ticker") or obj.get("symbol")
            score = obj.get("score")
            if isinstance(sym, str) and sym.strip().upper() in tickers:
                try:
                    sc = float(score)
                except (TypeError, ValueError):
                    sc = None
                if sc is not None and sc >= 100:
                    hits[sym.strip().upper()] = {
                        "tag": "scanner_score_saturated",
                        "score": sc,
                        "reading": ("the score=100 saturation zone was negative "
                                    "across train and holdout in the replay "
                                    "(-7.01pp train, -3.80pp holdout) — a "
                                    "warning on this member, not a filter"),
                    }
            for v in obj.values():
                walk(v, depth + 1)
        elif isinstance(obj, list):
            for v in obj[:5000]:
                walk(v, depth + 1)

    walk(data)
    return hits


# ── pool-level statistics ───────────────────────────────────────────────────


def pool_stats(members: Sequence[TickerDepth], pool: Sequence[TickerDepth],
               sectors: Mapping[str, str], price_ann: Mapping[str, dict],
               analyst: Mapping[str, dict], earnings: Mapping[str, dict],
               fundamentals: Mapping[str, dict],
               overlaps: Mapping[str, Any]) -> dict[str, Any]:
    """Shape of the published pool, and how it compares with membership.

    The comparison matters: the pool is a random draw, so a reader is entitled
    to know whether the draw looks like the thing it was drawn from.
    """
    def sector_mix(names: Iterable[str]) -> dict[str, Any]:
        c = Counter(sectors.get(t, "UNKNOWN") for t in names)
        total = sum(c.values()) or 1
        top = c.most_common()
        return {
            "counts": dict(top),
            "largest_share_pct": round(100.0 * top[0][1] / total, 1) if top else None,
            "largest_sector": top[0][0] if top else None,
            "unknown_share_pct": round(100.0 * c.get("UNKNOWN", 0) / total, 1),
            "distinct_sectors": len([k for k in c if k != "UNKNOWN"]),
        }

    pool_names = [d.ticker for d in pool]
    member_names = [d.ticker for d in members]

    bands = Counter(price_ann.get(t, {}).get("liquidity_band", "unknown")
                    for t in pool_names)
    atrs = sorted(a for a in (price_ann.get(t, {}).get("atr14_pct")
                              for t in pool_names) if a is not None)
    dvols = sorted(v for v in (price_ann.get(t, {}).get("dollar_volume_median_20d")
                               for t in pool_names) if v is not None)

    def q(xs: list[float], p: float):
        if not xs:
            return None
        i = min(len(xs) - 1, max(0, int(round(p * (len(xs) - 1)))))
        return round(float(xs[i]), 2)

    attention_tags = Counter(analyst.get(t, {}).get("tag", "no_coverage_data")
                             for t in pool_names)
    earn_tags = Counter(earnings.get(t, {}).get("tag", "no_data_in_cached_feed")
                        for t in pool_names)
    fund_tags = Counter(fundamentals.get(t, {}).get("tag", "no_fundamental_data")
                        for t in pool_names)
    flags = Counter()
    for t in pool_names:
        for f in price_ann.get(t, {}).get("risk_flags", []):
            flags[f] += 1

    return {
        "published_pool_size": len(pool_names),
        "membership_size": len(member_names),
        "sector_concentration_pool": sector_mix(pool_names),
        "sector_concentration_membership": sector_mix(member_names),
        "sector_draw_check": (
            "the pool is a random draw from membership, so its sector mix "
            "should resemble membership's; a large gap means the draw is "
            "unrepresentative, not that a sector is favoured"),
        "liquidity_distribution": dict(bands),
        "dollar_volume_musd": {"p10": q(dvols, 0.10), "median": q(dvols, 0.50),
                               "p90": q(dvols, 0.90)},
        "volatility_atr_pct": {"p10": q(atrs, 0.10), "median": q(atrs, 0.50),
                               "p90": q(atrs, 0.90)},
        "risk_flag_counts": dict(flags),
        "analyst_attention_tags": dict(attention_tags),
        "names_with_any_rating_change": sum(
            v for k, v in attention_tags.items()
            if k in ("rating_raised", "rating_cut", "rating_changes_both_ways")),
        "earnings_tags": dict(earn_tags),
        "earnings_feed_note": (
            "the earnings column is only as current as the cached calendar the "
            "surprise study fetched; when that feed ends months before this "
            "session every name reads as stale, which is the honest answer and "
            "not a finding about the names"),
        "names_with_usable_earnings_data": sum(
            v for k, v in earn_tags.items() if k in ("beat", "miss", "in_line",
                                                     "eps_beat_revenue_miss")),
        "fundamental_quality_tags": dict(fund_tags),
        "names_with_fundamental_quality": fund_tags.get("quality_pass", 0),
        "overlap_with_live_surfaces": overlaps,
    }


def overlap_block(root: Path, pool_names: set[str]) -> dict[str, Any]:
    """How much of the pool the live surfaces already name. Read-only."""
    out: dict[str, Any] = {}
    for label, rel in OVERLAP_ARTIFACTS.items():
        names, status = artifact_tickers(root, rel)
        hit = sorted(pool_names & names)
        out[label] = {
            "artifact": rel,
            "status": status,
            "tickers_in_artifact": len(names),
            "overlap_count": len(hit),
            "overlap_pct_of_pool": (round(100.0 * len(hit) / len(pool_names), 1)
                                    if pool_names else 0.0),
            "overlap_sample": hit[:20],
        }
    out["reading"] = (
        "Overlap is context, not endorsement in either direction. A name the "
        "live surfaces already carry is not thereby better, and one they do "
        "not carry is not thereby worse.")
    return out


# ── forward-tracking scaffold ───────────────────────────────────────────────


def forward_rows(pool: Sequence[TickerDepth], price_ann: Mapping[str, dict],
                 session: date) -> list[dict]:
    """One unresolved row per member, ready for later measurement.

    A scaffold, explicitly not evidence: the horizons are empty, the pool is
    equal-weight, and the row says so. Nothing here may be merged into the
    Phase 4B forward ledger — that ledger measures a different object under
    different rules, and pooling the two would contaminate both.
    """
    rows = []
    for d in pool:
        ann = price_ann.get(d.ticker, {})
        rows.append({
            "kind": "M1_SHADOW_POOL_FORWARD_ROW",
            "session": session.isoformat(),
            "ticker": d.ticker,
            "reference_close": ann.get("price"),
            "reference_bar": ann.get("last_bar"),
            "weight": "equal",
            "membership_basis": "m1_12_1_quintile",
            "horizons_sessions": list(FORWARD_HORIZONS),
            "forward_returns_pct": {f"{h}d": None for h in FORWARD_HORIZONS},
            "benchmarks": {b: {f"{h}d": None for h in FORWARD_HORIZONS}
                           for b in ("SPY", "QQQ")},
            "resolved": False,
            "measure_pool_not_names": (
                "the unit of measurement is the POOL mean/median against the "
                "same-session benchmarks; per-name outcomes are recorded but "
                "carry no claim, because M1's ticker-clustered interval is "
                "negative at every horizon"),
            "not_live_evidence": True,
            "not_phase4b": True,
            "research_only": True,
        })
    return rows


def append_forward_rows(root: Path, rows: Sequence[dict]) -> dict[str, Any]:
    """Append-only, idempotent per (session, ticker)."""
    path = root / FORWARD_ROWS_REL
    assert_replay_write_path(path, root=root)
    path.parent.mkdir(parents=True, exist_ok=True)
    seen: set[tuple[str, str]] = set()
    for r in _read_jsonl(path):
        seen.add((str(r.get("session")), str(r.get("ticker"))))
    added = 0
    with path.open("a") as fh:
        for r in rows:
            key = (str(r["session"]), str(r["ticker"]))
            if key in seen:
                continue
            fh.write(json.dumps(r) + "\n")
            seen.add(key)
            added += 1
    return {"path": FORWARD_ROWS_REL, "rows_appended": added,
            "rows_already_present": len(rows) - added,
            "idempotent_key": "(session, ticker)"}


# ── human research checklist ────────────────────────────────────────────────

CHECKLIST_FIELDS: tuple[tuple[str, str], ...] = (
    ("why_is_it_moving",
     "What actually moved this name over the last twelve months? Sector, "
     "product, balance sheet, or a single gap?"),
    ("latest_catalyst",
     "Most recent identifiable event, with its date. If none, say none."),
    ("earnings_revenue_trend",
     "Direction of revenue and margin over the last four quarters."),
    ("analyst_attention",
     "Has coverage changed recently, and in which direction? (Attention, not "
     "direction, is what the evidence supports.)"),
    ("liquidity_tradability",
     "Could a real position be entered and exited without moving the book?"),
    ("dilution_debt_risk",
     "Share count trend and debt against cash."),
    ("extended_or_exhausted",
     "Distance from MA50/MA200, position against the 52-week high, daily range."),
    ("what_invalidates_it",
     "The specific observation that would remove this name from consideration."),
)


def checklist_for(ticker: str) -> dict[str, Any]:
    """Empty fields for a human to fill. The report never fills them."""
    return {
        "ticker": ticker,
        "fields": {k: None for k, _ in CHECKLIST_FIELDS},
        "instruction": ("These are questions, not findings. The report supplies "
                        "facts and flags; the judgement is the reader's."),
    }


# ── build ───────────────────────────────────────────────────────────────────


def build_pool_report(root: Path, args) -> dict[str, Any]:
    """Gate, draw, annotate. Raises :class:`M1DataRefusal` before any write."""
    from research.backtests.alpha_reconstruction_lab import load_sector_map

    required = _required_session()
    excl = load_quarantine_excludes(root)
    t0 = time.time()
    depths = scan_cache(root, limit=args.limit, exclude=excl)
    refresh = load_refresh_outcome(root)
    audit = evaluate(depths, required_session=required, refresh=refresh,
                     cache_present=bool(depths), quarantined=len(excl))
    # THE GATE. Nothing below this line runs on data the guard refused, and
    # nothing is written if it raises.
    require_m1_data_ready(audit=audit)

    members, membership_meta = m1_membership(depths, required)
    if len(members) < POOL_MIN:
        raise M1DataRefusal(
            f"M1 membership is {len(members)} names, below the {POOL_MIN}-name "
            "floor for a wide pool — publishing fewer would read as a shortlist")

    size = max(POOL_MIN, min(int(args.size), POOL_MAX))
    pool, draw_meta = draw_pool(members, size=size, session=required, seed=args.seed)
    names = [d.ticker for d in pool]
    name_set = set(names)

    price_ann = price_annotations(root, names)
    analyst = analyst_attention(root, name_set, required)
    earnings = earnings_surprise(root, name_set, required)
    funds = fundamental_quality(root, names, required)
    earnings_horizon = earnings_feed_asof(root)
    saturated = saturation_warnings(root, name_set)
    sectors = load_sector_map(root)
    overlaps = overlap_block(root, name_set)
    stats = pool_stats(members, pool, sectors, price_ann, analyst, earnings,
                       funds, overlaps)
    stats["earnings_feed_last_report_date"] = earnings_horizon
    stats["earnings_feed_sessions_behind"] = (
        (required - date.fromisoformat(earnings_horizon)).days
        if earnings_horizon else None)

    entries = []
    for d in pool:
        t = d.ticker
        entries.append({
            "ticker": t,
            "sector": sectors.get(t, "UNKNOWN"),
            "tags": {
                "analyst_attention": analyst.get(t, {"tag": "no_coverage_data"}),
                "earnings_surprise": earnings.get(
                    t, {"tag": "no_data_in_cached_feed"}),
                "fundamental_quality": funds.get(t, {"tag": "no_fundamental_data"}),
                "liquidity": {
                    "band": price_ann.get(t, {}).get("liquidity_band"),
                    "dollar_volume_median_20d_musd": price_ann.get(t, {}).get(
                        "dollar_volume_median_20d"),
                    "fake_liquidity_ratio": price_ann.get(t, {}).get(
                        "fake_liquidity_ratio"),
                },
                "volatility_exhaustion": {
                    "atr14_pct": price_ann.get(t, {}).get("atr14_pct"),
                    "pct_vs_ma50": price_ann.get(t, {}).get("pct_vs_ma50"),
                    "pct_vs_ma200": price_ann.get(t, {}).get("pct_vs_ma200"),
                    "pct_below_52w_high": price_ann.get(t, {}).get(
                        "pct_below_52w_high"),
                    "risk_flags": price_ann.get(t, {}).get("risk_flags", []),
                },
                "scanner_saturation_warning": saturated.get(t),
                "live_surface_overlap": sorted(
                    label for label, block in overlaps.items()
                    if isinstance(block, dict) and t in (block.get("overlap_sample") or [])),
                "data_quality_warnings": price_ann.get(t, {}).get("data_quality", []),
            },
            "reference": {
                "close": price_ann.get(t, {}).get("price"),
                "last_bar": price_ann.get(t, {}).get("last_bar"),
                "bars": price_ann.get(t, {}).get("bars"),
            },
            "checklist": checklist_for(t),
            "position_in_this_list_means_nothing": True,
        })

    rows = forward_rows(pool, price_ann, required)
    payload: dict[str, Any] = {
        "kind": "M1_SHADOW_SOURCE_POOL",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "session": required.isoformat(),
        "note": NOTE,
        "data_integrity_header": audit.header,
        "guard_status": audit.status.value,
        "guard_warnings": audit.warnings,
        "membership": membership_meta,
        "draw": draw_meta,
        "pool_unordered": sorted(names),
        "display_order": "alphabetical — carries no information",
        "entries": entries,
        "pool_stats": stats,
        "forward_scaffold": {
            "horizons_sessions": list(FORWARD_HORIZONS),
            "rows_prepared": len(rows),
            "unit_of_measurement": "the pool, equal-weight, against SPY/QQQ",
            "not_merged_into_phase4b": True,
            "no_edge_claimed": (
                "these rows are unresolved. No forward claim is made, and none "
                "may be made from a single session's pool."),
        },
        "forbidden_by_design": [
            "never ranks or orders the pool",
            "never builds a conviction tier, a top-10 or a top-25 slice",
            "never publishes per-name momentum (it would invite a re-sort)",
            "never emits a trade signal, entry, stop, target or size",
            "never emits a live verdict token",
        ],
        "provider_calls": 0,
        "elapsed_sec": round(time.time() - t0, 1),
        **provenance_flags(),
    }
    assert_provenance(payload)
    payload["_forward_rows"] = rows
    return payload


# ── rendering ───────────────────────────────────────────────────────────────


def render_text(p: Mapping[str, Any]) -> str:
    h = p["data_integrity_header"]
    st = p["pool_stats"]
    L: list[str] = []
    A = L.append
    A("=" * 78)
    A("M1 SHADOW SOURCE POOL — wide, equal-weight, UNORDERED research pool")
    A("=" * 78)
    A(NOTE)
    A("")
    A(f"Session {p['session']} · generated {p['generated_at'][:19]}Z · "
      f"provider calls {p['provider_calls']}")
    A("")
    A("-" * 78)
    A("1. DATA INTEGRITY HEADER")
    A("-" * 78)
    for k in ("status", "may_run", "universe_size", "names_with_enough_bars",
              "coverage_pct", "median_bars", "stale_count", "shallow_count",
              "contamination_pct", "m1_pool_size_if_run", "refusal_reason"):
        A(f"  {k:<26} {h.get(k)}")
    for w in p.get("guard_warnings") or []:
        A(f"  ~ {w}")
    A("")
    A("-" * 78)
    A("2. M1 SOURCE POOL")
    A("-" * 78)
    m, d = p["membership"], p["draw"]
    A(f"  membership (12-1 quintile)  {m['members']} of {m['eligible_names']} eligible")
    A(f"  published pool              {d['published_pool_size']} names")
    A(f"  selection                  {d['selection']} (seed {d['seed']})")
    A(f"  display order              {d['display_order']}")
    A(f"  momentum distribution      {m['membership_momentum_distribution_pct']}")
    A(f"  {m['ordering_note']}")
    A("")
    names = p["pool_unordered"]
    for i in range(0, len(names), 10):
        A("    " + "  ".join(f"{n:<7}" for n in names[i:i + 10]))
    A("")
    A("  Equal weight. No name in this list is a better idea than another, and")
    A("  its position is alphabetical. If you find yourself reading the top of")
    A("  the list as more important, the list is being misread.")
    A("")
    A("-" * 78)
    A("3. TAGS PER NAME (annotations — none of them is a score)")
    A("-" * 78)
    A(f"  {'ticker':<8}{'sector':<16}{'analyst attention':<26}"
      f"{'earnings':<34}{'quality':<20}{'liquidity':<23}risk flags")
    for e in p["entries"]:
        t = e["tags"]
        A(f"  {e['ticker']:<8}{str(e['sector'])[:15]:<16}"
          f"{str(t['analyst_attention'].get('tag'))[:25]:<26}"
          f"{str(t['earnings_surprise'].get('tag'))[:33]:<34}"
          f"{str(t['fundamental_quality'].get('tag'))[:19]:<20}"
          f"{str(t['liquidity'].get('band'))[:22]:<23}"
          f"{','.join(t['volatility_exhaustion']['risk_flags']) or '-'}")
        extra = []
        if t.get("scanner_saturation_warning"):
            extra.append("SCANNER SCORE SATURATED (historically negative cohort)")
        if t.get("data_quality_warnings"):
            extra.append("data: " + ",".join(t["data_quality_warnings"]))
        if t.get("live_surface_overlap"):
            extra.append("also named by: " + ",".join(t["live_surface_overlap"]))
        for x in extra:
            A(f"           ! {x}")
    A("")
    A("-" * 78)
    A("4. POOL-LEVEL STATISTICS")
    A("-" * 78)
    sp = st["sector_concentration_pool"]
    sm = st["sector_concentration_membership"]
    A(f"  sector concentration (pool)       largest {sp['largest_sector']} "
      f"{sp['largest_share_pct']}% · {sp['distinct_sectors']} sectors · "
      f"unknown {sp['unknown_share_pct']}%")
    A(f"  sector concentration (membership) largest {sm['largest_sector']} "
      f"{sm['largest_share_pct']}% · {sm['distinct_sectors']} sectors")
    A(f"  liquidity bands                   {st['liquidity_distribution']}")
    A(f"  dollar volume (median 20d, $M)    {st['dollar_volume_musd']}")
    A(f"  volatility (ATR14 %)              {st['volatility_atr_pct']}")
    A(f"  risk flags                        {st['risk_flag_counts']}")
    A(f"  analyst attention tags            {st['analyst_attention_tags']}")
    A(f"  names with any rating change      {st['names_with_any_rating_change']}")
    A(f"  earnings tags                     {st['earnings_tags']}")
    A(f"  cached earnings feed ends         {st['earnings_feed_last_report_date']} "
      f"({st['earnings_feed_sessions_behind']} days before this session)")
    A(f"  {st['earnings_feed_note']}")
    A(f"  names with usable earnings data   {st['names_with_usable_earnings_data']}")
    A(f"  fundamental quality tags          {st['fundamental_quality_tags']}")
    A(f"  names passing fundamental quality {st['names_with_fundamental_quality']}")
    A("")
    A("  Overlap with live surfaces (context, not endorsement):")
    for label, b in st["overlap_with_live_surfaces"].items():
        if not isinstance(b, dict):
            continue
        A(f"    {label:<20} {b['status']:<11} {b['overlap_count']} of "
          f"{len(p['pool_unordered'])} ({b['overlap_pct_of_pool']}%)")
    A("")
    A("-" * 78)
    A("5. FORWARD-TRACKING SCAFFOLD")
    A("-" * 78)
    fs = p["forward_scaffold"]
    A(f"  horizons (sessions)        {fs['horizons_sessions']}")
    A(f"  rows prepared              {fs['rows_prepared']} (unresolved)")
    A(f"  unit of measurement        {fs['unit_of_measurement']}")
    A(f"  {fs['no_edge_claimed']}")
    A("  These rows are NOT part of the Phase 4B forward ledger and must never")
    A("  be pooled with it.")
    A("")
    A("-" * 78)
    A("6. HUMAN RESEARCH CHECKLIST")
    A("-" * 78)
    A("  For every name above, answer these before doing anything with it.")
    A("  The report supplies facts and flags; the judgement is yours.")
    for k, prompt in CHECKLIST_FIELDS:
        A(f"    - {k}: {prompt}")
    A("")
    A("=" * 78)
    A("WHAT THIS REPORT IS NOT")
    A("=" * 78)
    for x in p["forbidden_by_design"]:
        A(f"  - it {x}")
    A("")
    A("  M1's edge is a basket property. Its ticker-clustered interval is")
    A("  negative at every horizon tested, which means the basket beat the tape")
    A("  while the typical name in it did not. That is the whole reason this")
    A("  report is a pool and not a list of ideas.")
    A("=" * 78)
    return "\n".join(L)


def render_doc(p: Mapping[str, Any]) -> str:
    h = p["data_integrity_header"]
    st = p["pool_stats"]
    m, d = p["membership"], p["draw"]
    L: list[str] = []
    A = L.append
    A("# M1 Shadow Source Pool")
    A("")
    A(f"*Session {p['session']}. Generated {p['generated_at'][:19]}Z by "
      "`research/backtests/m1_shadow_pool.py`. Zero provider calls.*")
    A("")
    A("> **RESEARCH ONLY — this is a POOL, not a list of ideas.** Membership is "
      "the only claim. Names are alphabetical; position carries no information. "
      "No ranking, no conviction tiers, no scores, no trade signals, and no "
      "live verdict. Must never be pooled with the live forward ledger, program "
      "verdicts, HC/EO routing, Alpha Focus, MCP or the dashboard.")
    A("")
    A("## Why a pool and not a shortlist")
    A("")
    A("M1 (the 12-1 momentum quintile) is the only strategy family in this "
      "programme with out-of-sample support, and it works as a **basket**. Its "
      "ticker-clustered confidence interval is negative at every horizon "
      "tested: the basket beats the tape while the typical name inside it does "
      "not. Thirteen conviction overlays, an earnings-surprise dataset and a "
      "complete analyst-action study all failed to rank names inside it. So "
      "this report publishes membership and refuses to order it.")
    A("")
    A("## 1. Data integrity")
    A("")
    A("| field | value |")
    A("|---|---|")
    for k in ("status", "may_run", "universe_size", "names_with_enough_bars",
              "coverage_pct", "median_bars", "stale_count", "shallow_count",
              "contamination_pct", "m1_pool_size_if_run", "refusal_reason"):
        A(f"| {k} | {h.get(k)} |")
    A("")
    A(f"The guard (`m1_data_guard`) ran first and returned "
      f"`{p['guard_status']}`. Had it refused, this document would not exist.")
    A("")
    A("## 2. The pool")
    A("")
    A(f"- membership (12-1 quintile): **{m['members']}** of {m['eligible_names']} "
      "eligible names")
    A(f"- published: **{d['published_pool_size']}**, {d['selection']} "
      f"(seed {d['seed']}, {d['seed_rule']})")
    A(f"- {d['why_random']}")
    A("")
    A("```")
    names = p["pool_unordered"]
    for i in range(0, len(names), 10):
        A("  ".join(f"{n:<7}" for n in names[i:i + 10]))
    A("```")
    A("")
    A("## 3. Tags")
    A("")
    A("Annotations, not scores. Nothing here is combined into a number.")
    A("")
    A("| ticker | sector | analyst attention | earnings | quality | liquidity | risk flags |")
    A("|---|---|---|---|---|---|---|")
    for e in p["entries"]:
        t = e["tags"]
        warn = " ⚠︎" if t.get("scanner_saturation_warning") else ""
        A(f"| {e['ticker']}{warn} | {e['sector']} | "
          f"{t['analyst_attention'].get('tag')} | "
          f"{t['earnings_surprise'].get('tag')} | "
          f"{t['fundamental_quality'].get('tag')} | "
          f"{t['liquidity'].get('band')} | "
          f"{', '.join(t['volatility_exhaustion']['risk_flags']) or '—'} |")
    A("")
    A("⚠︎ = sits in the old scanner's score=100 saturation zone, one of the two "
      "cohorts the replay found reliably negative. Carried as a warning, not a "
      "filter — this report annotates, it does not remove.")
    A("")
    A("## 4. Pool statistics")
    A("")
    sp = st["sector_concentration_pool"]
    A(f"- sector concentration: largest **{sp['largest_sector']}** at "
      f"{sp['largest_share_pct']}%, {sp['distinct_sectors']} sectors "
      f"(membership: {st['sector_concentration_membership']['largest_share_pct']}%)")
    A(f"- liquidity: {st['liquidity_distribution']}")
    A(f"- dollar volume $M (p10/median/p90): {st['dollar_volume_musd']}")
    A(f"- ATR14 % (p10/median/p90): {st['volatility_atr_pct']}")
    A(f"- risk flags: {st['risk_flag_counts']}")
    A(f"- names with a rating change in {ATTENTION_WINDOW} sessions: "
      f"**{st['names_with_any_rating_change']}**")
    A(f"- names with usable earnings data: **{st['names_with_usable_earnings_data']}** "
      f"(the cached calendar ends {st['earnings_feed_last_report_date']}, "
      f"{st['earnings_feed_sessions_behind']} days before this session — "
      "so the earnings column is stale by construction, which is a fact about "
      "the feed and not about the names)")
    A(f"- names passing fundamental quality: "
      f"**{st['names_with_fundamental_quality']}**")
    A("")
    A("| live surface | status | overlap |")
    A("|---|---|---|")
    for label, b in st["overlap_with_live_surfaces"].items():
        if isinstance(b, dict):
            A(f"| {label} | {b['status']} | {b['overlap_count']} "
              f"({b['overlap_pct_of_pool']}%) |")
    A("")
    A("## 5. Forward scaffold")
    A("")
    fs = p["forward_scaffold"]
    A(f"{fs['rows_prepared']} unresolved rows at horizons "
      f"{fs['horizons_sessions']} sessions, measured as "
      f"{fs['unit_of_measurement']}. {fs['no_edge_claimed']} These rows are "
      "**not** part of the Phase 4B forward ledger.")
    A("")
    A("## 6. Human research checklist")
    A("")
    for k, prompt in CHECKLIST_FIELDS:
        A(f"- **{k.replace('_', ' ')}** — {prompt}")
    A("")
    A("## What this report is not")
    A("")
    for x in p["forbidden_by_design"]:
        A(f"- It {x}.")
    A("")
    return "\n".join(L)


# ── stage ───────────────────────────────────────────────────────────────────


def report_stage(args) -> int:
    root = Path(args.root)
    tw = LiveArtifactTripwire.snapshot(root)
    try:
        payload = build_pool_report(root, args)
    except M1DataRefusal as e:
        print("REFUSED — the M1 shadow pool did not run and wrote nothing:")
        print(f"  {e}")
        print(tw.report())
        tw.assert_clean()
        return 2

    rows = payload.pop("_forward_rows")
    text = assert_clean_language(render_text(payload), where="text report")
    doc = assert_clean_language(render_doc(payload), where="markdown report")
    payload["forward_scaffold"]["ledger"] = append_forward_rows(root, rows)
    payload["artifacts"] = [LATEST_REL, FORWARD_ROWS_REL, REPORT_TXT_REL, DOC_REL]
    assert_clean_language(json.dumps(payload), where="json payload")

    write_replay_json(root / LATEST_REL, payload, root=root)
    write_replay_text(root / REPORT_TXT_REL, text + "\n", root=root)
    write_replay_text(root / DOC_REL, doc, root=root)
    print(text)
    print()
    print(tw.report())
    tw.assert_clean()
    print(f"wrote {LATEST_REL}, {REPORT_TXT_REL}, {DOC_REL}, "
          f"{FORWARD_ROWS_REL} (+{payload['forward_scaffold']['ledger']['rows_appended']} rows)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("stage", nargs="?", default="report", choices=["report"])
    p.add_argument("--root", default=str(ROOT))
    p.add_argument("--size", type=int, default=POOL_DEFAULT,
                   help=f"published pool size, clamped to [{POOL_MIN}, {POOL_MAX}]")
    p.add_argument("--seed", type=int, default=None,
                   help="override the session-derived draw seed (research only)")
    p.add_argument("--limit", type=int, default=None,
                   help="scan only the first N cached series (debugging)")
    return p


def main(argv: list[str] | None = None) -> int:
    return run_cli(report_stage, build_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
