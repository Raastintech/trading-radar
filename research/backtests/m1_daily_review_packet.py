"""Daily M1 manual-review packet (research-only, cache-only by default).

What this is
------------
One operator-run command that assembles everything a human needs to review the
M1 source pool by hand — and nothing that would do the reviewing for them.

The M1 programme's settled position is narrow and this packet stays inside it:
12-1 momentum works as a **wide, unordered source pool**; ranking inside that
pool is not supported by the evidence (M1's ticker-clustered interval is
negative at every horizon tested); and earnings-surprise and analyst-action
data are **annotations**, never conviction. So the packet describes, counts and
tabulates. It never orders, scores, tiers or recommends.

What it will not do
-------------------
* no trade signal, entry, stop, target or size;
* no rank, score, tier, "top", "best" or conviction language — the rendered
  output is passed through a language guard before it is written;
* no per-name 12-1 momentum, which would invite a re-sort of an unordered pool;
* no re-roll of the pool: the draw is seeded off the session date, and this
  module deliberately exposes no ``--seed``;
* no provider call by default, and none at all without a flag that currently
  refuses.

Stages, in order
----------------
1. **Guard.** ``m1_data_guard`` runs first. If it refuses, the packet is a
   refusal packet explaining why, and no pool is formed.
2. **Refresh plan.** ``m1_price_refresh`` in plan mode only. It reports what a
   refresh would cost and prints the command, and does not run it.
3. **Pool.** A same-session shadow-pool artifact is reused if one exists;
   otherwise the pool is rebuilt in memory from the same deterministic draw.
4. **Packet.** Header, how-to-read, pool-level summary, name table with blank
   manual-bucket columns, review checklist, and a paste block for an external
   assistant.
5. **Cohort.** Read-only status of the frozen manual-pick cohort. Never
   modified here.

Writes ONLY:
    docs/research/M1_DAILY_REVIEW_PACKET.md
    cache/research/m1_daily_review_packet_latest.json
    logs/m1_daily_review_packet_latest.txt

Usage::

    GEM_TRADER_SKIP_DOTENV=true .venv/bin/python -m research.backtests.m1_daily_review_packet
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping, Sequence

from research.backtests.common import (
    FetchNotAuthorised,
    LiveArtifactTripwire,
    ROOT,
    add_safety_args,
    assert_provenance,
    provenance_flags,
    run_cli,
    write_replay_json,
    write_replay_text,
)
from research.backtests.m1_data_guard import (
    M1DataAudit,
    _required_session,
    run_audit,
)
from research.backtests.m1_price_refresh import plan_refresh
from research.backtests.m1_shadow_pool import (
    NEGATION_CUES,
    NEGATION_WINDOW,
    POOL_DEFAULT,
    ForbiddenLanguage,
    assert_clean_language,
)
from research.backtests.common import offline_env

offline_env()

LATEST_REL = "cache/research/m1_daily_review_packet_latest.json"
REPORT_TXT_REL = "logs/m1_daily_review_packet_latest.txt"
DOC_REL = "docs/research/M1_DAILY_REVIEW_PACKET.md"

POOL_ARTIFACT_REL = "cache/research/m1_shadow_pool_latest.json"
COHORT_GLOB = "research/backtests/cohorts/m1_manual_picks_*.json"
COHORT_RESOLUTION_REL = "cache/research/m1_manual_picks_resolution_latest.json"

#: Recommendation vocabulary the shadow pool's own list does not cover. Checked
#: with the same negation logic, so the packet can still say what it is NOT
#: ("this is not a buy list") while never making the claim itself.
PACKET_FORBIDDEN: tuple[str, ...] = (
    "buy list", "buy the", "sell the", "short the", "go long",
    "hold rating", "overweight", "underweight", "accumulate",
    "conviction score", "ranked by", "sorted by strength", "strongest",
    "highest quality name", "our pick",
)

NOTE = (
    "M1 DAILY REVIEW PACKET — RESEARCH ONLY. A manual-review worksheet built "
    "from an UNORDERED M1 source pool. Membership is the only claim it carries: "
    "no rank, no score, no tier, no trade signal and no live verdict. Tags are "
    "annotations, not conviction. Forward edge in M1 is a property of the POOL, "
    "not of any individual name. NOT live forward evidence and NOT backtest "
    "evidence for Phase 4B. Must never be pooled with the live forward ledger, "
    "program verdicts, HC/EO routing, Alpha Focus, MCP or the dashboard."
)

CHATGPT_INSTRUCTION = (
    "Help me manually classify these unordered M1 source-pool names into "
    "Research Now, Watch, and Reject. Do not rank them. Do not give trade "
    "recommendations. Focus on catalyst, setup quality, risk, and what needs "
    "manual verification."
)

CHECKLIST_QUESTIONS: tuple[str, ...] = (
    "Why is it moving?",
    "What is the recent catalyst?",
    "Is the catalyst scheduled or unscheduled?",
    "Is the trend early, continuation, pullback, or exhausted?",
    "Is liquidity real?",
    "Is there dilution/debt risk?",
    "Is there analyst attention?",
    "Is there fundamental quality?",
    "Is there sector/theme support?",
    "What confirms the setup?",
    "What invalidates the setup?",
    "What would make me reject it?",
)

MANUAL_BUCKETS = ("Research Now", "Watch", "Reject")

#: Below this, a refresh is described as OPTIONAL. The guard refusing is the
#: only thing that makes one required, and the guard says so itself.
REFRESH_OPTIONAL_MAX_CALLS = 150


class PacketRefusal(RuntimeError):
    """The packet cannot be built as asked (bad date, or no pool for it)."""


def assert_packet_language(text: str, *, where: str) -> str:
    """Shared doctrine first, then the packet's own vocabulary."""
    assert_clean_language(text, where=where)
    low = text.lower()
    bad: list[str] = []
    for phrase in PACKET_FORBIDDEN:
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


# ── read-only annotation sources ────────────────────────────────────────────


def company_profiles(root: Path, tickers: Sequence[str]) -> dict[str, dict[str, str]]:
    """Company name and industry from the Gatekeeper profile cache (read-only).

    CURRENT-METADATA APPROXIMATION, exactly as ``load_sector_map`` is: the
    label is what the provider reports today, not what it was at any past
    session. Descriptive only — nothing here feeds a filter.
    """
    import sqlite3  # noqa: PLC0415

    db = root / "db" / "trading.db"
    want = {t.upper() for t in tickers}
    out: dict[str, dict[str, str]] = {}
    if not db.exists():
        return out
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            for key, payload in con.execute(
                "SELECT key, payload FROM cache_meta WHERE key LIKE 'fmp:profile:%'"
            ):
                sym = str(key).split(":")[-1].upper()
                if sym not in want:
                    continue
                try:
                    d = json.loads(payload)
                except Exception:
                    continue
                if isinstance(d, dict):
                    out[sym] = {"company_name": str(d.get("companyName") or "") or None,
                                "industry": str(d.get("industry") or "") or None}
        finally:
            con.close()
    except Exception:
        return out
    return out


# ── step 2: refresh planning (never executes) ───────────────────────────────


def refresh_block(root: Path) -> dict[str, Any]:
    """What a refresh would cost. Plan mode only — no call is ever made here."""
    plan = plan_refresh(root)
    planned = int(plan["planned_calls"])
    lag = plan.get("tickers_provider_lag_deferred", [])
    return {
        "mode": "plan_only",
        "provider_calls_made": 0,
        "planned_calls": planned,
        "tickers_stale": plan.get("tickers_stale", []),
        "tickers_shallow": plan.get("tickers_shallow", []),
        "tickers_missing": plan.get("tickers_missing", []),
        "tickers_young_listing_excluded": plan.get("tickers_young_listing_excluded", []),
        "tickers_provider_lag_deferred": lag,
        "n_stale": plan.get("n_stale", 0),
        "n_shallow": plan.get("n_shallow", 0),
        "n_missing": plan.get("n_missing", 0),
        "n_young_listing_excluded": plan.get("n_young_listing_excluded", 0),
        "n_provider_lag_deferred": plan.get("n_provider_lag_deferred", 0),
        "necessity": ("optional" if planned <= REFRESH_OPTIONAL_MAX_CALLS
                      else "large — review before running"),
        "operator_command": (
            "SNIPER_ENV_PATH=/home/gem/secure/trading.env .venv/bin/python -m "
            f"research.backtests.m1_price_refresh fetch --execute-fetch "
            f"--max-calls {max(planned, 1)}"
        ) if planned else None,
        "this_module_did_not_run_it": True,
    }


# ── step 3: the pool ────────────────────────────────────────────────────────


def load_or_build_pool(root: Path, *, target: date, size: int
                       ) -> tuple[dict[str, Any], str]:
    """Reuse a same-session pool artifact, else rebuild the same seeded draw."""
    art = root / POOL_ARTIFACT_REL
    if art.exists():
        try:
            payload = json.loads(art.read_text())
        except Exception:
            payload = None
        if isinstance(payload, dict) and payload.get("session") == target.isoformat():
            return payload, "reused_existing_artifact"

    if target != _required_session():
        raise PacketRefusal(
            f"no shadow-pool artifact for {target.isoformat()}, and the pool "
            "can only be rebuilt for the current required session "
            f"({_required_session().isoformat()}). A pool for a past session "
            "cannot be reconstructed after the fact without re-dating the draw, "
            "which would not be the pool that session published.")

    from research.backtests.m1_shadow_pool import build_pool_report  # noqa: PLC0415

    # No --seed is threaded through: the draw stays keyed to the session date so
    # nobody can re-roll it until they like the names.
    payload = build_pool_report(root, SimpleNamespace(limit=None, size=size, seed=None))
    payload.pop("_forward_rows", None)
    return payload, "built_in_memory_not_persisted"


# ── step 5: frozen cohort status (read-only) ────────────────────────────────


def _sessions_after(start: date, n: int) -> date:
    """Plain business-day count; holidays push the real date slightly later."""
    import pandas as pd  # noqa: PLC0415

    return pd.bdate_range(start=pd.Timestamp(start), periods=n + 1)[-1].date()


def cohort_block(root: Path) -> dict[str, Any]:
    """Status of the frozen manual-pick cohort. Never modified here."""
    files = sorted((root / "research/backtests/cohorts").glob("m1_manual_picks_*.json")) \
        if (root / "research/backtests/cohorts").is_dir() else []
    if not files:
        return {"present": False,
                "note": "no frozen manual-pick cohort in this checkout."}
    cohort = json.loads(files[-1].read_text())
    ref = cohort["picks"][0]["reference_bar"] if cohort.get("picks") else cohort["session"]
    ref_date = date.fromisoformat(ref)

    resolution = None
    res_path = root / COHORT_RESOLUTION_REL
    if res_path.exists():
        try:
            resolution = json.loads(res_path.read_text())
        except Exception:
            resolution = None

    return {
        "present": True,
        "session": cohort["session"],
        "picks": [p["ticker"] for p in cohort.get("picks", [])],
        "n_picks": cohort.get("n_picks"),
        "n_control_not_picked": cohort.get("n_control"),
        "hypothesis": cohort.get("hypothesis"),
        "decisive_horizons": cohort.get("decisive_horizons"),
        "maturity_dates_business_day_estimate": {
            f"{h}d": _sessions_after(ref_date, h).isoformat()
            for h in cohort.get("horizons_sessions", [])
        },
        "current_status": (resolution or {}).get("verdict", "NOT_RESOLVED_YET"),
        "status_reason": (resolution or {}).get("reason"),
        "read_only_here": True,
        "not_modified_by_this_packet": True,
    }


# ── step 4: the packet ──────────────────────────────────────────────────────


def _flags(entry: Mapping[str, Any]) -> list[str]:
    ve = (entry.get("tags") or {}).get("volatility_exhaustion") or {}
    return list(ve.get("risk_flags") or [])


def name_rows(pool: Mapping[str, Any], profiles: Mapping[str, dict]
              ) -> list[dict[str, Any]]:
    """One row per pool name. Blank manual fields; no score, rank or tier."""
    rows = []
    for e in pool.get("entries", []):
        t = e["ticker"]
        tags = e.get("tags") or {}
        prof = profiles.get(t, {})
        liq = tags.get("liquidity") or {}
        ve = tags.get("volatility_exhaustion") or {}
        rows.append({
            "ticker": t,
            "company_name": prof.get("company_name"),
            "sector": e.get("sector", "UNKNOWN"),
            "industry": prof.get("industry"),
            "liquidity_band": liq.get("band"),
            "dollar_volume_median_20d_musd": liq.get("dollar_volume_median_20d_musd"),
            "volatility_exhaustion_flags": _flags(e),
            "atr14_pct": ve.get("atr14_pct"),
            "analyst_attention": (tags.get("analyst_attention") or {}).get("tag"),
            "earnings_surprise": (tags.get("earnings_surprise") or {}).get("tag"),
            "fundamental_quality": (tags.get("fundamental_quality") or {}).get("tag"),
            "live_surface_overlap": tags.get("live_surface_overlap") or [],
            "scanner_saturation_warning": tags.get("scanner_saturation_warning"),
            "data_quality_warnings": tags.get("data_quality_warnings") or [],
            # Left blank on purpose: the operator fills these in by hand. A
            # pre-filled bucket would be this module making the judgement it
            # exists to hand over.
            "manual_bucket": "",
            "manual_notes": "",
        })
    return sorted(rows, key=lambda r: r["ticker"])


def summary_block(pool: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]
                  ) -> dict[str, Any]:
    """Counts and distributions. Descriptive only — nothing is combined."""
    def tally(key):
        out: dict[str, int] = {}
        for r in rows:
            v = r.get(key) or "UNKNOWN"
            out[str(v)] = out.get(str(v), 0) + 1
        return dict(sorted(out.items(), key=lambda kv: (-kv[1], kv[0])))

    flag_counts: dict[str, int] = {}
    for r in rows:
        for f in r["volatility_exhaustion_flags"]:
            flag_counts[f] = flag_counts.get(f, 0) + 1

    overlap_counts: dict[str, int] = {}
    for r in rows:
        for o in r["live_surface_overlap"]:
            overlap_counts[o] = overlap_counts.get(o, 0) + 1

    stats = pool.get("pool_stats") or {}
    return {
        "sector_concentration": tally("sector"),
        "industry_concentration": tally("industry"),
        "liquidity_distribution": tally("liquidity_band"),
        "volatility_exhaustion_flag_counts": flag_counts,
        "analyst_attention_counts": tally("analyst_attention"),
        "earnings_surprise_coverage_counts": tally("earnings_surprise"),
        "fundamental_quality_counts": tally("fundamental_quality"),
        "live_surface_overlap_counts": overlap_counts,
        "scanner_saturation_warning_count": sum(
            1 for r in rows if r.get("scanner_saturation_warning")),
        "data_quality_warning_count": sum(
            1 for r in rows if r.get("data_quality_warnings")),
        "names_with_any_exhaustion_flag": sum(
            1 for r in rows if r["volatility_exhaustion_flags"]),
        "earnings_feed_last_report_date": stats.get("earnings_feed_last_report_date"),
        "earnings_feed_sessions_behind": stats.get("earnings_feed_sessions_behind"),
        "concentration_caveat": (
            "Counts describe the drawn slice, not a preference. A sector that "
            "dominates the table dominates it because the uniform draw landed "
            "that way, and carries no information about the names in it."),
    }


def refusal_packet(root: Path, audit: M1DataAudit, target: date,
                   reason: str) -> dict[str, Any]:
    payload = {
        "kind": "M1_DAILY_REVIEW_PACKET_REFUSED",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "session": target.isoformat(),
        "note": NOTE,
        "may_run": False,
        "guard_status": audit.status.value,
        "refusal_reason": reason,
        "all_refusal_reasons": [r.value for r in audit.refusals],
        "guard_warnings": audit.warnings,
        "data_integrity_header": audit.header,
        "pool_emitted": None,
        "pool_size": 0,
        "why_no_pool": (
            "The data guard refused. Forming a pool on data the guard rejected "
            "would publish names the cache cannot support, which is the exact "
            "failure the guard exists to prevent."),
        "refresh": refresh_block(root),
        "provider_calls": 0,
        **provenance_flags(),
    }
    assert_provenance(payload)
    return payload


def build_packet(root: Path, args) -> dict[str, Any]:
    """Assemble the packet. Reads only; the caller writes."""
    audit = run_audit(root)
    target = date.fromisoformat(args.date) if args.date else _required_session()

    if not audit.may_run:
        return refusal_packet(
            root, audit, target,
            audit.refusals[0].value if audit.refusals else "guard refused")

    pool, pool_source = load_or_build_pool(root, target=target,
                                           size=int(args.pool_size))
    names = list(pool.get("pool_unordered") or [])
    profiles = company_profiles(root, names)
    rows = name_rows(pool, profiles)

    payload: dict[str, Any] = {
        "kind": "M1_DAILY_REVIEW_PACKET",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "session": pool.get("session", target.isoformat()),
        "note": NOTE,
        "may_run": True,
        "guard_status": audit.status.value,
        "guard_warnings": audit.warnings,
        "data_integrity_header": audit.header,
        "pool_source": pool_source,
        "pool_is_new": pool_source != "reused_existing_artifact",
        "pool_size_emitted": len(rows),
        "m1_membership_size": (pool.get("membership") or {}).get(
            "membership_size", (pool.get("draw") or {}).get("membership_size")),
        "display_order": "alphabetical — carries no information",
        "how_to_read": [
            "This is not a buy list.",
            "This is not ranked. Position in the table carries no information.",
            "Names are not ordered by conviction, and no conviction tier exists.",
            "Tags are annotations only. None of them is a score, and none is combined into one.",
            "Manual research is required on every name before it means anything.",
            "Forward edge in M1 is measured at the POOL level, not as certainty about any individual name.",
        ],
        "refresh": refresh_block(root),
        "pool_summary": summary_block(pool, rows),
        "names": rows,
        "manual_buckets_available": list(MANUAL_BUCKETS),
        "manual_review_checklist": list(CHECKLIST_QUESTIONS),
        "cohort": cohort_block(root),
        "forbidden_by_design": [
            "never ranks, scores or orders the pool",
            "never builds a conviction tier or a top-N slice",
            "never publishes per-name 12-1 momentum",
            "never emits a trade signal, entry, stop, target or size",
            "never emits a live verdict token",
            "never runs a provider call by default",
        ],
        "provider_calls": 0,
        **provenance_flags(),
    }
    if args.include_chatgpt_brief:
        payload["chatgpt_brief"] = chatgpt_brief(payload)
    assert_provenance(payload)
    return payload


# ── rendering ───────────────────────────────────────────────────────────────


def chatgpt_brief(p: Mapping[str, Any]) -> str:
    """Compact paste block for an external assistant."""
    s = p["pool_summary"]
    L = [f"M1 source pool — session {p['session']} (unordered, not ranked)",
         f"guard: {p['guard_status']} | may_run: {p['may_run']} | "
         f"pool: {p['pool_size_emitted']} of {p['m1_membership_size']} members | "
         f"provider calls: {p['provider_calls']}",
         "sectors: " + ", ".join(f"{k} {v}" for k, v in
                                 list(s["sector_concentration"].items())[:8]),
         "warnings: " + ", ".join([
             f"exhaustion-flagged {s['names_with_any_exhaustion_flag']}",
             f"data-quality {s['data_quality_warning_count']}",
             f"scanner-saturation {s['scanner_saturation_warning_count']}"]),
         "",
         "NAMES  [sector | liquidity | analyst | quality | flags]"]
    for r in p["names"]:
        flags = ",".join(r["volatility_exhaustion_flags"]) or "-"
        L.append(f"{r['ticker']:<6} [{r['sector']} | {r['liquidity_band'] or '?'} | "
                 f"{r['analyst_attention'] or '?'} | {r['fundamental_quality'] or '?'} | {flags}]")
    L += ["", CHATGPT_INSTRUCTION]
    return "\n".join(L)


def _md_table(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    head = ("| ticker | company | sector | industry | liquidity | flags | analyst | "
            "earnings | quality | overlap | data warnings | bucket | notes |")
    L = [head, "|" + "---|" * 13]
    for r in rows:
        L.append("| {t} | {c} | {s} | {i} | {l} | {f} | {a} | {e} | {q} | {o} | {d} |  |  |".format(
            t=r["ticker"], c=(r["company_name"] or "—")[:38], s=r["sector"],
            i=(r["industry"] or "—")[:28], l=r["liquidity_band"] or "—",
            f=", ".join(r["volatility_exhaustion_flags"]) or "—",
            a=r["analyst_attention"] or "—", e=r["earnings_surprise"] or "—",
            q=r["fundamental_quality"] or "—",
            o=", ".join(r["live_surface_overlap"]) or "—",
            d=", ".join(r["data_quality_warnings"]) or "—"))
    return L


def render_doc(p: Mapping[str, Any]) -> str:
    if p["kind"].endswith("REFUSED"):
        return render_refusal_doc(p)
    h = p["data_integrity_header"]
    s = p["pool_summary"]
    L = [f"# M1 Daily Review Packet — session {p['session']}", "",
         f"*Generated {p['generated_at']} by `research/backtests/m1_daily_review_packet.py`. "
         f"Provider calls: {p['provider_calls']}.*", "",
         f"> **RESEARCH ONLY.** {NOTE}", "",
         "## 1. Header", "",
         "| field | value |", "|---|---|",
         f"| session | {p['session']} |",
         f"| guard status | {p['guard_status']} |",
         f"| may_run | {p['may_run']} |",
         f"| pool size emitted | {p['pool_size_emitted']} |",
         f"| M1 membership size | {p['m1_membership_size']} |",
         f"| pool source | {p['pool_source']} |",
         f"| pool is new | {p['pool_is_new']} |",
         f"| provider calls made | {p['provider_calls']} |", ""]

    L += ["### Data integrity", "", "| field | value |", "|---|---|"]
    for k in ("status", "may_run", "universe_size", "names_with_enough_bars",
              "coverage_pct", "median_bars", "stale_count", "shallow_count",
              "contamination_pct", "cache_lag_sessions", "m1_pool_size_if_run",
              "refusal_reason"):
        if k in h:
            L.append(f"| {k} | {h[k]} |")
    L.append(f"| young_listings_excluded (refresh plan) | "
             f"{p['refresh']['n_young_listing_excluded']} |")
    if p["guard_warnings"]:
        L += ["", "**Guard warnings**", ""]
        L += [f"- {w}" for w in p["guard_warnings"]]

    L += ["", "## 2. How to read this", ""]
    L += [f"- {line}" for line in p["how_to_read"]]

    r = p["refresh"]
    L += ["", "## 3. Refresh status (planned only — nothing was fetched)", "",
          "| field | value |", "|---|---|",
          f"| planned calls | {r['planned_calls']} |",
          f"| necessity | {r['necessity']} |",
          f"| stale | {r['n_stale']} |",
          f"| shallow | {r['n_shallow']} |",
          f"| missing | {r['n_missing']} |",
          f"| young listings excluded | {r['n_young_listing_excluded']} |",
          f"| provider-lag deferred | {r['n_provider_lag_deferred']} |",
          f"| provider calls made | {r['provider_calls_made']} |"]
    if r["operator_command"]:
        L += ["", "If you decide a refresh is worth it, this is the command — "
                  "this module did not run it:", "", "```bash",
              r["operator_command"], "```"]
    else:
        L += ["", "Nothing is queued: no name in the cache needs a call today."]

    L += ["", "## 4. Pool-level summary", "",
          f"*{s['concentration_caveat']}*", "",
          "| dimension | counts |", "|---|---|"]
    for label, key in (("sector", "sector_concentration"),
                       ("industry", "industry_concentration"),
                       ("liquidity", "liquidity_distribution"),
                       ("analyst attention", "analyst_attention_counts"),
                       ("earnings coverage", "earnings_surprise_coverage_counts"),
                       ("fundamental quality", "fundamental_quality_counts"),
                       ("exhaustion flags", "volatility_exhaustion_flag_counts"),
                       ("live-surface overlap", "live_surface_overlap_counts")):
        items = s[key]
        rendered = ", ".join(f"{k} {v}" for k, v in list(items.items())[:10]) or "—"
        L.append(f"| {label} | {rendered} |")
    L += [f"| names with any exhaustion flag | {s['names_with_any_exhaustion_flag']} |",
          f"| data-quality warnings | {s['data_quality_warning_count']} |",
          f"| scanner-saturation warnings | {s['scanner_saturation_warning_count']} |"]

    L += ["", "## 5. Name table", "",
          "Alphabetical. Position carries no information. `bucket` and `notes` "
          "are yours to fill in — this module leaves them blank on purpose.", "",
          f"Buckets: {' / '.join(MANUAL_BUCKETS)}", ""]
    L += _md_table(p["names"])

    L += ["", "## 6. Manual review checklist", "",
          "For every name you seriously like, answer these before doing anything "
          "with it:", ""]
    L += [f"{i}. {q}" for i, q in enumerate(p["manual_review_checklist"], 1)]

    c = p["cohort"]
    L += ["", "## 7. Frozen manual-pick cohort (read-only here)", ""]
    if not c.get("present"):
        L.append(c.get("note", "no cohort."))
    else:
        L += ["| field | value |", "|---|---|",
              f"| cohort session | {c['session']} |",
              f"| picks | {c['n_picks']} |",
              f"| control (not picked) | {c['n_control_not_picked']} |",
              f"| current status | {c['current_status']} |",
              f"| decisive horizons | {c['decisive_horizons']} |", ""]
        L += [f"Selected names: {', '.join(c['picks'])}", "",
              "Maturity (business-day estimate; holidays push these later):", ""]
        L += [f"- **{k}** — {v}" for k, v in
              c["maturity_dates_business_day_estimate"].items()]
        L += ["", "This packet does not modify the frozen cohort."]

    if p.get("chatgpt_brief"):
        L += ["", "## 8. Paste this into ChatGPT", "", "```text",
              p["chatgpt_brief"], "```"]
    return "\n".join(L) + "\n"


def render_refusal_doc(p: Mapping[str, Any]) -> str:
    h = p["data_integrity_header"]
    L = [f"# M1 Daily Review Packet — REFUSED — session {p['session']}", "",
         f"*Generated {p['generated_at']}. Provider calls: {p['provider_calls']}.*", "",
         f"> **RESEARCH ONLY.** {NOTE}", "",
         "## The guard refused; no pool was formed", "",
         "| field | value |", "|---|---|",
         f"| guard status | {p['guard_status']} |",
         f"| may_run | {p['may_run']} |",
         f"| refusal reason | {p['refusal_reason']} |",
         f"| all refusal reasons | {', '.join(p['all_refusal_reasons']) or '—'} |", "",
         p["why_no_pool"], "", "### Data integrity", "",
         "| field | value |", "|---|---|"]
    for k, v in h.items():
        if isinstance(v, (str, int, float, bool)) or v is None:
            L.append(f"| {k} | {v} |")
    r = p["refresh"]
    L += ["", "### What a refresh would cost (planned only — nothing fetched)", "",
          f"- planned calls: **{r['planned_calls']}**",
          f"- stale {r['n_stale']} / shallow {r['n_shallow']} / missing {r['n_missing']}",
          f"- provider-lag deferred: {r['n_provider_lag_deferred']}"]
    if r["operator_command"]:
        L += ["", "```bash", r["operator_command"], "```"]
    return "\n".join(L) + "\n"


def render_text(p: Mapping[str, Any]) -> str:
    L = ["=" * 78, "M1 DAILY REVIEW PACKET" + (" — REFUSED" if p["kind"].endswith("REFUSED") else ""),
         "=" * 78, NOTE, "",
         f"  session                    {p['session']}",
         f"  guard status               {p['guard_status']}",
         f"  may_run                    {p['may_run']}",
         f"  provider calls             {p['provider_calls']}"]
    if p["kind"].endswith("REFUSED"):
        L += [f"  refusal reason             {p['refusal_reason']}", "",
              "  No pool was formed.", "=" * 78]
        return "\n".join(L)
    s = p["pool_summary"]
    L += [f"  pool size emitted          {p['pool_size_emitted']}",
          f"  M1 membership size         {p['m1_membership_size']}",
          f"  pool source                {p['pool_source']}",
          f"  refresh planned calls      {p['refresh']['planned_calls']} "
          f"({p['refresh']['necessity']})",
          "",
          "  SECTORS  " + ", ".join(f"{k} {v}" for k, v in
                                    list(s["sector_concentration"].items())[:8]),
          "  LIQUIDITY " + ", ".join(f"{k} {v}" for k, v in
                                     s["liquidity_distribution"].items()),
          f"  exhaustion-flagged {s['names_with_any_exhaustion_flag']} | "
          f"data-quality warnings {s['data_quality_warning_count']}",
          "",
          f"  cohort                     {p['cohort'].get('current_status', 'n/a')}",
          "",
          "  Names are alphabetical and unordered. Fill the bucket and notes",
          "  columns in the document by hand; nothing here does it for you.",
          "=" * 78]
    return "\n".join(L)


# ── stage ───────────────────────────────────────────────────────────────────


def packet_stage(args) -> int:
    root = Path(args.root)
    if getattr(args, "execute_refresh", False):
        raise FetchNotAuthorised(
            "--execute-refresh is not approved for this module. The packet is "
            "cache-only by design; run m1_price_refresh directly, deliberately, "
            "with its own cap. No calls were made.")

    tripwire = LiveArtifactTripwire.snapshot(root)
    payload = build_packet(root, args)

    doc = assert_packet_language(render_doc(payload), where=DOC_REL)
    text = assert_packet_language(render_text(payload), where=REPORT_TXT_REL)
    if payload.get("chatgpt_brief"):
        assert_packet_language(payload["chatgpt_brief"], where="chatgpt_brief")

    write_replay_json(root / LATEST_REL, payload, root=root)
    write_replay_text(root / DOC_REL, doc, root=root)
    write_replay_text(root / REPORT_TXT_REL, text, root=root)

    print(text)
    print(f"\nwrote {LATEST_REL}, {DOC_REL}, {REPORT_TXT_REL}")
    print(f"\n{tripwire.report()}")
    tripwire.assert_clean()
    return 0 if payload["may_run"] else 3


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("stage", nargs="?", default="packet", choices=["packet"])
    p.add_argument("--date", default=None,
                   help="session to build for (YYYY-MM-DD). Defaults to the "
                        "guard's required session.")
    p.add_argument("--pool-size", type=int, default=POOL_DEFAULT,
                   help=f"pool size when the pool must be rebuilt (default {POOL_DEFAULT}).")
    p.add_argument("--cache-only", dest="cache_only", action="store_true", default=True,
                   help="cache-only. This is the default and the only supported mode.")
    p.add_argument("--include-chatgpt-brief", dest="include_chatgpt_brief",
                   action="store_true", default=True,
                   help="include the paste block (default on).")
    p.add_argument("--no-chatgpt-brief", dest="include_chatgpt_brief",
                   action="store_false", help="omit the paste block.")
    p.add_argument("--plan-refresh-only", action="store_true", default=True,
                   help="plan the refresh, never run it. This is the default "
                        "and cannot currently be turned off.")
    p.add_argument("--execute-refresh", action="store_true",
                   help="NOT APPROVED — refuses. Present so the refusal is "
                        "explicit rather than a missing flag.")
    add_safety_args(p, fetches=False)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run_cli(packet_stage, args)
    except PacketRefusal as e:
        print(f"\nREFUSED: {e}")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
