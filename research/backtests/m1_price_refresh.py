"""M1 deep price-history refresh (research-only, plan-first, fetch explicitly gated).

The M1 data guard (``research/backtests/m1_data_guard.py``) decides whether
12-1 momentum can be computed today. This module is what fixes it when it
cannot: it plans, and — only under an explicit ``--execute-fetch`` — performs,
the minimum deep-history refresh the guard needs.

Namespace decision
------------------
Bars are written to **``cache/replay_prices/``** and nowhere else.

That is the survivorship-corrected deep cache the four completed M1 studies
read: 7,474 usable series at a median 1,678 bars, already running past the
2025-12-31 replay window to 2026-09-04 because the original backfill fetched
to ``FETCH_TO``. Trailing bars beyond the window are therefore the cache's
intended shape, not a novelty, and every replay stage slices its own window.
``cache/prices`` (median 113 bars) and ``cache/prices_deep`` (411 files) cannot
support 12-1 and are never written here — they belong to the live scanner and
the ``FORBIDDEN_WRITE_PREFIXES`` guard in ``common.py`` enforces that.

Reproducibility is protected by making the refresh **append-forward-only**:

* only bars strictly after a series' existing last bar are added;
* any overlapping bar whose close disagrees with what is already stored is a
  ``HISTORY_DRIFT`` — almost always the provider re-adjusting for a split. The
  ticker is refused, not silently rewritten, because a silent rewrite changes
  what four completed studies measured. Repairing one is a deliberate act
  behind ``--allow-history-rewrite``.

So a run of this module cannot alter any historical bar the M1 programme was
built on, and the guard refuses outright if a drift is ever recorded.

Stages
------
``plan``    Zero provider calls. Audits the cache, works out which tickers need
            bars and why, and prints the estimate: tickers, calls, destination,
            budget impact, failure behaviour. **This is the default.**
``fetch``   Requires ``--execute-fetch``. Capped before the first call.

Writes ONLY:
    cache/replay_prices/{SYM}.parquet                  (fetch only)
    cache/research/m1_price_refresh_latest.json
    cache/research/m1_price_refresh_intermediates/*    (fetch progress ledger)
    logs/m1_price_refresh_latest.txt

Usage::

    GEM_TRADER_SKIP_DOTENV=true .venv/bin/python -m research.backtests.m1_price_refresh plan
    SNIPER_ENV_PATH=/home/gem/secure/trading.env .venv/bin/python \\
        -m research.backtests.m1_price_refresh fetch --execute-fetch --max-calls 500
"""

from __future__ import annotations

import argparse
import json
import re
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from core.provider_budget import (  # noqa: E402
    PLANNED_CALL_CONFIRM_THRESHOLD,
    assert_planned_calls_authorised,
    assert_within_hard_cap,
)
from research.backtests.common import (
    FetchNotAuthorised,
    LiveArtifactTripwire,
    ROOT,
    add_safety_args,
    assert_provenance,
    assert_replay_write_path,
    enforce_call_cap,
    offline_env,
    provenance_flags,
    require_execute_fetch,
    run_cli,
    write_replay_json,
    write_replay_text,
)
from research.backtests.m1_data_guard import (
    INACTIVE_AFTER_CALENDAR_DAYS,
    M1_MIN_BARS_REQUIRED,
    PRICES_REL,
    TickerDepth,
    _required_session,
    load_quarantine_excludes,
    scan_cache,
)

offline_env()

LATEST_REL = "cache/research/m1_price_refresh_latest.json"
REPORT_TXT_REL = "logs/m1_price_refresh_latest.txt"
INTERMEDIATES_REL = "cache/research/m1_price_refresh_intermediates"
PROGRESS_REL = f"{INTERMEDIATES_REL}/refresh_progress.jsonl"
PROVIDER_LAG_REL = "cache/research/m1_price_refresh_provider_lag.json"

#: The endpoint the original replay backfill used. One call returns a symbol's
#: full daily history, so a refresh costs exactly one call per ticker whether
#: it needs one bar or a thousand.
ENDPOINT = "/historical-price-eod/full"
FETCH_FROM = "2020-01-01"

DEFAULT_MAX_CALLS = 500
RATE_PER_MIN = 600  # safety margin under the provider's 750 RPM ceiling

#: Per-run ceiling on top of --max-calls. Until 2026-09-09 ``--max-calls`` was
#: unbounded, so one flag could authorise a five-figure run against a provider
#: budget nothing else enforced (3,257 calls went through on 2026-09-09; a
#: manual study spent 20,260 on 2026-09-05). A universe-wide catch-up is a real
#: need, so this is a ceiling with a named override rather than a hard stop.
HARD_MAX_CALLS = 1200

#: Close-price agreement tolerance on overlapping bars, as a fraction. Floating
#: -point round-trips through parquet and JSON move a close by far less than
#: this; a split re-adjustment moves it by orders of magnitude more.
DRIFT_TOLERANCE = 1e-4

#: Placeholder keys that must never reach the provider. ``offline_env()``
#: installs "offline"; ``tests/conftest.py`` installs a stub. Either one
#: authenticates as nobody, so the fetch refuses rather than burning calls.
OFFLINE_SENTINELS: frozenset[str] = frozenset({"offline", "test_fmp_key", "dummy",
                                               "changeme", "none", "null"})

NOTE = (
    "M1 PRICE REFRESH — RESEARCH ONLY. Deep price-history maintenance for a "
    "shadow research report. Emits no signal, no score, no ranking and no "
    "verdict. NOT live forward evidence and NOT backtest evidence for Phase 4B. "
    "Writes bars only into cache/replay_prices (append-forward-only); touches "
    "no live scanner, HC/EO, Alpha Focus, program, routing, MCP, dashboard or "
    "ledger artifact."
)

#: A symbol is joined to the cache directory to form a write path, so it has to
#: be validated before it can become one. FMP symbols are alphanumerics plus dot
#: and dash (BRK.B, RDS-A); anything else — a slash, a traversal, a space — is an
#: operator error rather than a ticker, and is refused at the input boundary
#: instead of being discovered at the write.
TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,11}$")


def valid_ticker(sym: object) -> bool:
    """True for something that can safely be used as a cache filename."""
    return bool(TICKER_RE.match(str(sym).strip().upper()))


#: A name listed too recently to HAVE the required depth cannot be deepened by
#: refetching — the bars do not exist yet. Measured 2026-09-08: all 89 shallow
#: names in the queue were young listings, so 89 of 108 planned calls would have
#: returned NO_NEW_BARS. 273 sessions is roughly 400 calendar days; anything
#: that started trading inside that window is excluded from the queue and
#: counted separately, so the plan states what a refresh can actually fix.
YOUNG_LISTING_CALENDAR_DAYS = 400

#: How long a name confirmed to be PROVIDER-LAGGED stays out of the queue.
#:
#: A "stale" name is one whose cached last bar is behind the required session.
#: The queue assumes that means OUR cache is behind — but on 2026-09-08 all 19
#: stale names came back NO_NEW_BARS, and a direct probe of AVB showed the
#: provider itself holding no bar after 2026-08-24. The cache was level with
#: the provider; there was nothing to fetch. Left alone, those 19 names would
#: be re-queued every single day, spending ~400 calls a month to be told the
#: same thing.
#:
#: So a NO_NEW_BARS is recorded as evidence about the PROVIDER, and the name is
#: deferred until this many days have passed. Seven days keeps the worst-case
#: catch-up lag immaterial for a 273-bar momentum window while cutting the
#: steady-state cost of a permanently-lagged name by ~86%.
PROVIDER_LAG_RECHECK_DAYS = 7

REASON_STALE = "stale_behind_required_session"
REASON_SHALLOW = "shallow_below_m1_minimum"
REASON_MISSING = "missing_no_parquet"


# ── planning (zero provider calls) ──────────────────────────────────────────


def load_provider_lag(root: Path) -> dict[str, dict[str, Any]]:
    """Read the provider-lag memo. Missing or malformed reads as empty.

    Fail-open is deliberate: a memo this module cannot read must cost calls,
    never data. The worst case of ignoring it is the old behaviour.
    """
    path = root / PROVIDER_LAG_REL
    if not path.exists():
        return {}
    try:
        doc = json.loads(path.read_text())
    except Exception:
        return {}
    entries = doc.get("entries") if isinstance(doc, dict) else None
    if not isinstance(entries, dict):
        return {}
    return {str(k).upper(): v for k, v in entries.items() if isinstance(v, dict)}


def provider_lag_defers(entry: dict[str, Any] | None, *,
                        cache_last_bar: str | None, required: date) -> bool:
    """True when a memo entry still vouches for skipping this ticker.

    Three ways an entry stops counting, all of them fail-open:

    * the cache moved on since the memo was written — the memo describes a
      series that no longer exists, so it says nothing about today;
    * the re-check date has arrived;
    * anything about the entry is unreadable.
    """
    if not isinstance(entry, dict):
        return False
    if entry.get("cache_last_bar") != cache_last_bar:
        return False
    try:
        return required < date.fromisoformat(str(entry["recheck_on"]))
    except Exception:
        return False


def build_provider_lag_entry(*, provider_last_bar: str, cache_last_bar: str | None,
                             observed_on: date,
                             previous: dict[str, Any] | None = None) -> dict[str, Any]:
    """One memo entry: what the provider had, when we looked, when to look again."""
    prior = 0
    if isinstance(previous, dict) and \
            previous.get("provider_last_bar") == provider_last_bar:
        try:
            prior = int(previous.get("confirmations") or 0)
        except (TypeError, ValueError):
            prior = 0
    return {
        "provider_last_bar": provider_last_bar,
        "cache_last_bar": cache_last_bar,
        "observed_on": observed_on.isoformat(),
        "recheck_on": (observed_on + timedelta(days=PROVIDER_LAG_RECHECK_DAYS)).isoformat(),
        "confirmations": prior + 1,
    }


def save_provider_lag(root: Path, entries: dict[str, dict[str, Any]],
                      *, now: datetime | None = None) -> Path:
    """Persist the memo. Guarded write; replay namespace only."""
    stamp = (now or datetime.now(timezone.utc)).isoformat()
    return write_replay_json(root / PROVIDER_LAG_REL, {
        "kind": "m1_price_refresh_provider_lag",
        "updated_at": stamp,
        "recheck_days": PROVIDER_LAG_RECHECK_DAYS,
        "note": (
            "Evidence about the PROVIDER, not about our cache. A ticker listed "
            "here returned NO_NEW_BARS: the provider's own last bar matched the "
            "one already cached, so there was nothing to fetch. It is kept out "
            "of the refresh queue until recheck_on. Deleting this file is safe "
            "— it only costs calls."
        ),
        "research_only": True,
        "entries": dict(sorted(entries.items())),
    }, root=root)



def _is_young_listing(d: TickerDepth, required: date) -> bool:
    """True when the series is short because the name is new, not because bars
    are missing. Such a name cannot be deepened by refetching."""
    if not d.first_bar:
        return False
    try:
        first = date.fromisoformat(d.first_bar)
    except ValueError:
        return False
    return (required - first).days < YOUNG_LISTING_CALENDAR_DAYS


class _Flag:
    """Tiny holder so plan_refresh can be called with or without the flag."""

    def __init__(self, value: bool = False):
        self.value = value


def plan_refresh(root: Path, *, now: datetime | None = None,
                 extra_tickers: list[str] | None = None,
                 limit: int | None = None,
                 only_stale: bool = False,
                 recheck_provider_lag: bool = False,
                 only_tickers: list[str] | None = None) -> dict[str, Any]:
    """Work out what needs fetching, and what it would cost. No calls made."""
    args_only_stale = _Flag(only_stale)
    required = _required_session(now)
    lag_memo = {} if recheck_provider_lag else load_provider_lag(root)
    prices_dir = root / PRICES_REL
    cache_present = prices_dir.is_dir()

    excl = load_quarantine_excludes(root)
    depths: list[TickerDepth] = (
        scan_cache(root, limit=limit, exclude=excl) if cache_present else []
    )
    by_ticker = {d.ticker: d for d in depths}

    stale: list[str] = []
    lagged: list[str] = []
    shallow: list[str] = []
    young: list[str] = []
    for d in depths:
        if not d.passes_floor or not d.readable:
            continue
        if not d.is_active(required):
            # Delisted / no longer trading: refetching cannot produce new bars,
            # so spending a call on one is pure waste.
            continue
        if d.is_stale(required):
            stale.append(d.ticker)
            if provider_lag_defers(lag_memo.get(d.ticker),
                                   cache_last_bar=d.last_bar, required=required):
                # Stale, but a previous fetch proved the provider has no newer
                # bar either. Still counted as stale — that is the cache's real
                # state — but not queued, because the call is known to be a
                # no-op until the provider moves.
                lagged.append(d.ticker)
        elif not d.depth_ok:
            if _is_young_listing(d, required):
                # Shallow because it has not existed long enough. A refetch
                # returns the same bars; the queue would be spending calls to
                # be told nothing changed.
                young.append(d.ticker)
            else:
                shallow.append(d.ticker)

    missing = sorted(
        {t.upper() for t in (extra_tickers or [])}
        - set(by_ticker)
        - set(excl)
    )

    queueable_stale = set(stale) - set(lagged)
    # A restricted queue is how a small tracked cohort stays current without
    # paying for the whole universe: every other exclusion still applies, this
    # only narrows what is left.
    restrict = {t.strip().upper() for t in (only_tickers or []) if t.strip()}
    if getattr(args_only_stale, "value", False):
        todo = sorted(queueable_stale)
    else:
        todo = sorted(queueable_stale | set(shallow) | set(missing))
    if restrict:
        todo = [t for t in todo if t in restrict]
    planned_calls = len(todo)

    return {
        "required_session": required.isoformat(),
        "cache_present": cache_present,
        "series_scanned": len(depths),
        "quarantined_excluded": len(excl),
        "tickers_stale": sorted(stale),
        "tickers_shallow": sorted(shallow),
        "tickers_missing": missing,
        "tickers_young_listing_excluded": sorted(young),
        "tickers_provider_lag_deferred": sorted(lagged),
        "n_stale": len(stale),
        "n_stale_queued": len(queueable_stale),
        "n_provider_lag_deferred": len(lagged),
        "provider_lag_rule": (
            f"a stale name whose last recorded fetch proved the provider has no "
            f"newer bar is re-checked every {PROVIDER_LAG_RECHECK_DAYS} days "
            f"instead of every run; --recheck-provider-lag ignores the memo"),
        "recheck_provider_lag": bool(recheck_provider_lag),
        "n_shallow": len(shallow),
        "n_missing": len(missing),
        "n_young_listing_excluded": len(young),
        "young_listing_rule": (
            f"a name whose first bar is inside {YOUNG_LISTING_CALENDAR_DAYS} "
            "calendar days cannot reach the depth floor by refetching, so it is "
            "not queued"),
        "only_stale": bool(only_stale),
        "only_tickers": sorted(restrict),
        "n_only_tickers": len(restrict),
        "n_tickers": planned_calls,
        "planned_calls": planned_calls,
        "calls_per_ticker": 1,
        "endpoint": ENDPOINT,
        "destination_path": PRICES_REL,
        "write_mode": "append-forward-only (merge on write; no historical rewrite)",
        "todo": todo,
    }


def budget_block(planned_calls: int, cap: int) -> dict[str, Any]:
    """Budget impact, stated without reading credentials or the env file."""
    return {
        "planned_calls": planned_calls,
        "call_cap_this_run": cap,
        "would_exceed_cap": planned_calls > cap,
        "monthly_budget_source": "core.config.FMP_MONTHLY_BUDGET (env-configured)",
        "monthly_budget_read_here": False,
        "budget_accounting": (
            "Each call is charged through core.data_gatekeeper "
            "(budget_consume(1) + log_endpoint), the same meter the live "
            "pipelines use, so this refresh is visible in fmp_budget_monthly "
            "rather than off-book."
        ),
        "comparison": (
            "For scale: the entire earnings-surprise study cost 52 calls, and "
            "the original full replay backfill was capped at 8,500."
        ),
        "steady_state_expectation": (
            "After a first catch-up, the daily cost is one call per ticker that "
            "actually fell behind the required session — 19 names on the "
            "2026-09-06 audit, not the whole universe."
        ),
    }


def failure_behaviour_block() -> dict[str, Any]:
    """What happens when a fetch goes wrong. Stated up front, not discovered."""
    return {
        "no_execute_fetch": (
            "Refuses before the first call, exits 3, reports the plan. This is "
            "the default path."
        ),
        "over_cap": "Aborts before the first call (enforce_call_cap), exits 3.",
        "http_or_transport_error": (
            "Ticker recorded as API_ERROR in the progress ledger and skipped. "
            "Existing bars are left untouched; the run continues."
        ),
        "empty_response": (
            "Recorded as EMPTY and skipped — a rename or dead symbol, not an "
            "error. Never written as an empty series."
        ),
        "history_drift": (
            "Overlapping bar disagrees with a stored close beyond "
            f"{DRIFT_TOLERANCE}: nothing is written for that ticker, it is "
            "recorded as HISTORY_DRIFT, and the data guard REFUSES the next "
            "shadow run outright. Repair requires --allow-history-rewrite."
        ),
        "no_new_bars": (
            "Recorded as NO_NEW_BARS. Not a failure; no write. The provider's "
            "own last bar is written to the provider-lag memo and the ticker "
            f"is kept out of the queue for {PROVIDER_LAG_RECHECK_DAYS} days, so "
            "a provider that is itself behind cannot re-charge the same call "
            "every run."
        ),
        "partial_run": (
            "Every ticker is written and ledgered independently, so an "
            "interrupted run leaves a consistent cache and a resumable ledger. "
            "The guard re-audits from the cache itself, never from this plan."
        ),
        "live_artifacts": (
            "LiveArtifactTripwire snapshots live artifacts before the stage and "
            "aborts (exit 4) if any moved. Writes outside the replay namespace "
            "raise ReplayWriteViolation (exit 5) before touching disk."
        ),
    }


def render_report(plan: dict[str, Any], budget: dict[str, Any],
                  result: dict[str, Any] | None) -> str:
    L: list[str] = []
    A = L.append
    A("=" * 78)
    A("M1 PRICE REFRESH — " + ("FETCH RESULT" if result else "PLAN (zero provider calls)"))
    A("=" * 78)
    A(NOTE)
    A("")
    A("ESTIMATE")
    A("-" * 78)
    A(f"  number of tickers          {plan['n_tickers']}")
    A(f"    stale (behind session)   {plan['n_stale']}")
    A(f"    shallow (< {M1_MIN_BARS_REQUIRED} bars)     {plan['n_shallow']}")
    A(f"    missing (no parquet)     {plan['n_missing']}")
    A(f"    young listings excluded  {plan.get('n_young_listing_excluded', 0)} "
      "(too new to reach the depth floor — a refetch cannot help)")
    n_lag = plan.get("n_provider_lag_deferred", 0)
    A(f"    provider-lagged deferred {n_lag} "
      f"(provider has no newer bar either; re-checked every "
      f"{PROVIDER_LAG_RECHECK_DAYS} days)")
    if plan.get("n_only_tickers"):
        A(f"    queue restricted to {plan['n_only_tickers']} named tickers "
          "(--only-tickers)")
    if plan.get("recheck_provider_lag"):
        A("    provider-lag memo IGNORED this run (--recheck-provider-lag)")
    if plan.get("only_stale"):
        A("    queue restricted to STALE names only (--only-stale)")
    A(f"  expected calls             {plan['planned_calls']} "
      f"({plan['calls_per_ticker']} per ticker, {plan['endpoint']})")
    A(f"  destination path           {plan['destination_path']}/{{SYM}}.parquet")
    A(f"  write mode                 {plan['write_mode']}")
    A(f"  required session           {plan['required_session']}")
    A(f"  series scanned             {plan['series_scanned']}")
    A(f"  quarantined excluded       {plan['quarantined_excluded']}")
    A("")
    A("BUDGET IMPACT")
    A("-" * 78)
    for k, v in budget.items():
        A(f"  {k:<26} {v}")
    A("")
    A("FAILURE BEHAVIOUR")
    A("-" * 78)
    for k, v in failure_behaviour_block().items():
        A(f"  {k:<26} {v}")
    A("")
    A("TICKERS (diagnostic — a maintenance queue, not a candidate list)")
    A("-" * 78)
    for key in ("tickers_stale", "tickers_shallow", "tickers_missing",
                "tickers_provider_lag_deferred"):
        v = plan.get(key) or []
        A(f"  {key:<26} {len(v)}: {', '.join(v[:20])}{' …' if len(v) > 20 else ''}")
    if result:
        A("")
        A("RESULT")
        A("-" * 78)
        for k in ("calls_made", "updated_count", "no_new_bars_count",
                  "bars_added", "failed_refresh_count", "missing_ticker_count",
                  "history_drift_count", "empty_count",
                  "provider_lag_recorded", "provider_lag_cleared",
                  "provider_lag_memo_size"):
            A(f"  {k:<26} {result.get(k)}")
        if result.get("history_drift_tickers"):
            A("")
            A("  !! HISTORY DRIFT — the provider disagrees with stored bars.")
            A("     Nothing was rewritten. The data guard will REFUSE the next run.")
            A(f"     {', '.join(result['history_drift_tickers'][:20])}")
    A("")
    A("This module maintains DATA ONLY. It computes no momentum, emits no")
    A("ranking, and says nothing about whether 12-1 works.")
    A("=" * 78)
    return "\n".join(L)


def _write_artifacts(root: Path, plan: dict[str, Any], budget: dict[str, Any],
                     result: dict[str, Any] | None, stage: str) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "kind": "M1_PRICE_REFRESH",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "stage": stage,
        "plan": {k: v for k, v in plan.items() if k != "todo"},
        "budget": budget,
        "failure_behaviour": failure_behaviour_block(),
        # Flat keys the data guard reads directly.
        "failed_refresh_count": (result or {}).get("failed_refresh_count", 0) if result else None,
        "missing_ticker_count": (result or {}).get("missing_ticker_count",
                                                   plan["n_missing"]) if result else plan["n_missing"],
        "history_drift_count": (result or {}).get("history_drift_count") if result else None,
        "result": result,
        "provider_calls": (result or {}).get("calls_made", 0) if result else 0,
        "cache_namespace": {
            "writes_bars": PRICES_REL,
            "writes_artifacts": [LATEST_REL, REPORT_TXT_REL, PROGRESS_REL],
            "never_writes": ["cache/prices", "cache/prices_deep",
                             "cache/backtest_prices", "data/", "db/"],
        },
        "emits_signal": False,
        "emits_ranking": False,
        "note": NOTE,
        **provenance_flags(),
    }
    assert_provenance(payload)
    write_replay_json(root / LATEST_REL, payload, root=root)
    write_replay_text(root / REPORT_TXT_REL, render_report(plan, budget, result),
                      root=root)
    return payload


def _only_tickers(args) -> list[str]:
    """Parse --only-tickers, refusing anything that is not a symbol."""
    raw = getattr(args, "only_tickers", None)
    if not raw:
        return []
    out = []
    for part in str(raw).split(","):
        t = part.strip().upper()
        if not t:
            continue
        if not valid_ticker(t):
            raise SystemExit(f"refusing --only-tickers value {part.strip()!r}: not a ticker.")
        out.append(t)
    return out


def plan_stage(args) -> int:
    root = Path(args.root)
    tripwire = LiveArtifactTripwire.snapshot(root)
    plan = plan_refresh(
        root, extra_tickers=_extra(args), limit=args.limit,
        only_stale=bool(getattr(args, "only_stale", False)),
        recheck_provider_lag=bool(getattr(args, "recheck_provider_lag", False)),
        only_tickers=_only_tickers(args))
    cap = args.max_calls if args.max_calls is not None else DEFAULT_MAX_CALLS
    budget = budget_block(plan["planned_calls"], cap)
    _write_artifacts(root, plan, budget, None, "plan")
    print(render_report(plan, budget, None))
    print(f"\n{tripwire.report()}")
    print("provider calls: 0 — this stage plans only. "
          "Re-run `fetch --execute-fetch` to authorise.")
    tripwire.assert_clean()
    return 0


def _extra(args) -> list[str]:
    if not getattr(args, "tickers", None):
        return []
    out: list[str] = []
    for raw in str(args.tickers).split(","):
        t = raw.strip().upper()
        if not t:
            continue
        if not valid_ticker(t):
            raise SystemExit(
                f"refusing --tickers value {raw.strip()!r}: not a ticker. A symbol "
                f"becomes a path component under {PRICES_REL}, so anything outside "
                "[A-Z0-9.-] is rejected here rather than at the write.")
        out.append(t)
    return out


# ── fetch (gated) ───────────────────────────────────────────────────────────


def merge_forward(existing, incoming, *, allow_rewrite: bool = False):
    """Append only bars newer than the stored series. Pure; no I/O.

    Returns ``(merged_or_None, status, n_added)``. ``None`` means do not write:
    either nothing is new, or the provider disagrees with stored history.
    """
    import pandas as pd

    if existing is None or len(existing) == 0:
        return incoming.sort_index(), "CREATED", len(incoming)

    ex = existing.sort_index()
    inc = incoming.sort_index()
    overlap = inc.index.intersection(ex.index)
    if len(overlap) and not allow_rewrite:
        a = ex.loc[overlap, "close"].astype(float)
        b = inc.loc[overlap, "close"].astype(float)
        denom = a.abs().where(a.abs() > 0, 1.0)
        if bool((((a - b).abs() / denom) > DRIFT_TOLERANCE).any()):
            return None, "HISTORY_DRIFT", 0

    new = inc.loc[inc.index > ex.index.max()]
    if len(new) == 0:
        return None, "NO_NEW_BARS", 0
    if allow_rewrite and len(overlap):
        merged = pd.concat([ex.drop(index=overlap), inc]).sort_index()
        return merged, "REWRITTEN", len(new)
    return pd.concat([ex, new]).sort_index(), "APPENDED", len(new)


def fetch_stage(args) -> int:
    root = Path(args.root)
    plan = plan_refresh(
        root, extra_tickers=_extra(args), limit=args.limit,
        only_stale=bool(getattr(args, "only_stale", False)),
        recheck_provider_lag=bool(getattr(args, "recheck_provider_lag", False)),
        only_tickers=_only_tickers(args))
    todo = plan["todo"]
    cap = args.max_calls if args.max_calls is not None else DEFAULT_MAX_CALLS
    budget = budget_block(len(todo), cap)

    print(render_report(plan, budget, None))

    if not todo:
        print("\nnothing to refresh — cache already satisfies the M1 guard "
              "(zero provider calls)")
        _write_artifacts(root, plan, budget,
                         {"calls_made": 0, "updated_count": 0, "bars_added": 0,
                          "no_new_bars_count": 0, "failed_refresh_count": 0,
                          "missing_ticker_count": 0, "history_drift_count": 0,
                          "empty_count": 0, "history_drift_tickers": []},
                         "fetch")
        return 0

    enforce_call_cap(len(todo), cap, what="M1 price refresh")
    # Two ceilings above the per-run --max-calls, both refusing before the first
    # call: a confirmation threshold for anything non-trivial, and a per-run hard
    # cap that a universe-wide catch-up has to name explicitly.
    assert_planned_calls_authorised(
        len(todo), override=bool(getattr(args, "allow_large_run", False)),
        where="M1 price refresh", override_flag="--allow-large-run")
    assert_within_hard_cap(
        len(todo), hard_cap=HARD_MAX_CALLS,
        override=bool(getattr(args, "allow_huge_run", False)),
        where="M1 price refresh", override_flag="--allow-huge-run")
    require_execute_fetch(args, planned_calls=len(todo), what="M1 price refresh")

    tripwire = LiveArtifactTripwire.snapshot(root)

    import os  # noqa: PLC0415
    import pandas as pd  # noqa: PLC0415
    import requests  # noqa: PLC0415

    # Credential resolution, and why it is written this way. This module calls
    # offline_env() at IMPORT time, which sets FMP_API_KEY to the string
    # "offline" so no analysis stage can reach a provider. A credential load
    # with override=False cannot displace that sentinel, so the fetch inherited
    # it and every call came back 401 — 108 of them on 2026-09-07, spending an
    # operator's approved budget to discover a problem that was knowable before
    # the first call. Hence: override=True, read the key from the environment
    # rather than from core.config (which may already be cached with the
    # sentinel), and REFUSE up front if what we hold cannot authenticate.
    ep_path = os.getenv("SNIPER_ENV_PATH", "").strip()
    if ep_path and Path(ep_path).exists():
        from dotenv import load_dotenv as _ld  # noqa: PLC0415

        _ld(ep_path, override=True)
    import core.config as cfg  # noqa: PLC0415
    from core.data_gatekeeper import get_gatekeeper  # noqa: PLC0415

    key = (os.environ.get("FMP_API_KEY") or "").strip()
    if not key or key in OFFLINE_SENTINELS:
        raise FetchNotAuthorised(
            "no usable FMP credential: FMP_API_KEY is "
            f"{'unset' if not key else 'the offline sentinel'}. Point "
            "SNIPER_ENV_PATH at the credential file. No calls were made.")
    base = (os.environ.get("FMP_BASE_URL") or cfg.FMP_BASE_URL).rstrip("/")
    gate = get_gatekeeper()

    prices_dir = root / PRICES_REL
    prices_dir.mkdir(parents=True, exist_ok=True)
    progress = root / PROGRESS_REL
    progress.parent.mkdir(parents=True, exist_ok=True)

    counts = {"calls_made": 0, "updated_count": 0, "bars_added": 0,
              "no_new_bars_count": 0, "failed_refresh_count": 0,
              "missing_ticker_count": 0, "history_drift_count": 0,
              "empty_count": 0, "provider_lag_recorded": 0,
              "provider_lag_cleared": 0}
    drift_tickers: list[str] = []
    # The memo is keyed to the planner's clock (the required session), not to
    # wall-clock now, so "deferred for N days" means the same N the planner
    # measures when it decides whether to re-queue.
    lag_entries = load_provider_lag(root)
    lag_observed_on = date.fromisoformat(plan["required_session"])
    stamps: list[float] = []

    for sym in todo:
        # Rate limit: stay under the provider's RPM ceiling.
        now = time.time()
        stamps[:] = [t for t in stamps if now - t < 60]
        if len(stamps) >= RATE_PER_MIN:
            time.sleep(max(60 - (now - stamps[0]), 0.01))
        stamps.append(time.time())

        row: dict[str, Any] = {"symbol": sym,
                               "fetched_at": datetime.now(timezone.utc).isoformat()}
        try:
            r = requests.get(f"{base}{ENDPOINT}",
                             params={"symbol": sym, "from": FETCH_FROM,
                                     "to": plan["required_session"], "apikey": key},
                             timeout=60)
            counts["calls_made"] += 1
            gate.budget_consume(1)
            gate.log_endpoint(ENDPOINT, saved=0, resp_bytes=len(r.content))
            if r.status_code != 200:
                row.update(status="API_ERROR", http=r.status_code)
                counts["failed_refresh_count"] += 1
                _append(progress, row)
                continue
            data = r.json()
        except Exception as e:
            counts["calls_made"] += 1
            row.update(status="API_ERROR", error=type(e).__name__)
            counts["failed_refresh_count"] += 1
            _append(progress, row)
            continue

        rows = sorted([x for x in (data or []) if x.get("date")],
                      key=lambda x: x["date"]) if isinstance(data, list) else []
        # Trailing zero-volume flat-OHLC padding is synthetic post-delisting
        # filler; leaving it in fabricates a return of exactly zero.
        while rows:
            b = rows[-1]
            if (b.get("volume") or 0) == 0 and \
                    b.get("open") == b.get("high") == b.get("low") == b.get("close"):
                rows.pop()
            else:
                break
        if not rows:
            row.update(status="EMPTY", rows=0)
            counts["empty_count"] += 1
            if sym in plan["tickers_missing"]:
                counts["missing_ticker_count"] += 1
            _append(progress, row)
            continue

        inc = pd.DataFrame([{ "date": b["date"], "open": b.get("open"),
                              "high": b.get("high"), "low": b.get("low"),
                              "close": b.get("close"), "volume": b.get("volume")}
                            for b in rows])
        inc["date"] = pd.to_datetime(inc["date"])
        inc = inc.set_index("date").sort_index()

        # The only bar-write in the M1 foundation. Guarded even though the
        # symbol was validated at the input boundary: a cache filename could
        # also arrive from a stray parquet stem, and a module whose premise is
        # write containment should not have its single write be the one place
        # that trusts its input.
        path = prices_dir / f"{sym}.parquet"
        assert_replay_write_path(path, root=root)
        existing = None
        if path.exists():
            try:
                existing = pd.read_parquet(path)
            except Exception:
                existing = None

        merged, status, added = merge_forward(
            existing, inc, allow_rewrite=bool(args.allow_history_rewrite))
        row.update(status=status, bars_added=added)
        if status == "HISTORY_DRIFT":
            counts["history_drift_count"] += 1
            drift_tickers.append(sym)
        elif status == "NO_NEW_BARS":
            counts["no_new_bars_count"] += 1
            # The call proved something about the PROVIDER: its own latest bar
            # is the one already cached. Record it so the next run does not buy
            # the same answer again.
            provider_last = str(inc.index.max())[:10]
            cache_last = (str(existing.sort_index().index.max())[:10]
                          if existing is not None and len(existing) else None)
            lag_entries[sym] = build_provider_lag_entry(
                provider_last_bar=provider_last, cache_last_bar=cache_last,
                observed_on=lag_observed_on, previous=lag_entries.get(sym))
            counts["provider_lag_recorded"] += 1
            row.update(provider_last_bar=provider_last,
                       recheck_on=lag_entries[sym]["recheck_on"])
        elif merged is not None:
            merged.to_parquet(path)
            counts["updated_count"] += 1
            counts["bars_added"] += added
            # The provider moved. Any memo about it is now history.
            if lag_entries.pop(sym, None) is not None:
                counts["provider_lag_cleared"] += 1
        _append(progress, row)

    save_provider_lag(root, lag_entries)
    result = {**counts, "history_drift_tickers": drift_tickers,
              "provider_lag_memo_size": len(lag_entries),
              "provider_lag_memo_path": PROVIDER_LAG_REL}
    _write_artifacts(root, plan, budget, result, "fetch")
    print(render_report(plan, budget, result))
    print(f"\n{tripwire.report()}")
    tripwire.assert_clean()
    return 0 if counts["history_drift_count"] == 0 else 1


def _append(path: Path, row: dict[str, Any]) -> None:
    assert_replay_write_path(path)
    with path.open("a") as fh:
        fh.write(json.dumps(row, default=str) + "\n")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("stage", nargs="?", default="plan", choices=["plan", "fetch"])
    p.add_argument("--tickers", default=None,
                   help="Comma-separated symbols to treat as required (missing "
                        "ones are planned as fetches).")
    p.add_argument("--limit", type=int, default=None,
                   help="Audit only the first N cached series (debugging).")
    p.add_argument("--only-stale", action="store_true",
                   help="queue only names behind the required session — the "
                        "subset a refetch can actually change.")
    p.add_argument("--only-tickers", default=None,
                   help="restrict the queue to these symbols (comma-separated). "
                        "Every other exclusion still applies; this only narrows "
                        "what is left. Used to keep a tracked cohort current "
                        "without refreshing the whole universe.")
    p.add_argument("--recheck-provider-lag", action="store_true",
                   help="ignore the provider-lag memo and re-queue every stale "
                        "name, including those a previous fetch proved the "
                        "provider cannot advance yet.")
    p.add_argument("--allow-history-rewrite", action="store_true",
                   help="Permit overwriting stored historical bars when the "
                        "provider disagrees (e.g. a genuine split "
                        "re-adjustment). Off by default: a silent rewrite "
                        "changes what the completed M1 studies measured.")
    p.add_argument("--allow-large-run", action="store_true",
                   help=f"authorise a run planning more than "
                        f"{PLANNED_CALL_CONFIRM_THRESHOLD} provider calls. "
                        "Without it such a run refuses before the first call.")
    p.add_argument("--allow-huge-run", action="store_true",
                   help=f"authorise a run above the per-run hard cap of "
                        f"{HARD_MAX_CALLS} calls (implies --allow-large-run "
                        "is also needed). For a deliberate universe-wide "
                        "catch-up, not for routine maintenance.")
    add_safety_args(p, fetches=True)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    stage = plan_stage if args.stage == "plan" else fetch_stage
    return run_cli(stage, args)


if __name__ == "__main__":
    raise SystemExit(main())
