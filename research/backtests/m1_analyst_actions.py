"""M1 Analyst-Action Momentum — do dated upgrades rank inside the M1 pool?

RESEARCH ONLY. Phase 1 of the analyst-action line, run under explicit operator
approval for ONE endpoint: FMP ``/stable/grades``.

Why this endpoint and no other. FMP's ``/stable/analyst-estimates`` is a
SNAPSHOT: its rows are keyed by fiscal period, carry no as-of date, and are
re-served at their current values, so point-in-time EPS/revenue estimate
revision breadth cannot be reconstructed from it. ``/stable/grades`` is the
opposite shape — one row per analyst ACTION, each with its own event date and
a ``previousGrade -> newGrade`` pair — so it can be replayed as-of without
lookahead. That makes analyst-action momentum the usable proxy for the
revision-momentum hypothesis, and this module tests it inside the frozen M1
12-1 momentum pool.

Provider policy, enforced in code rather than by intent:

* ``/stable/grades`` is the ONLY endpoint this module may touch
  (:data:`APPROVED_ENDPOINT`); ``grades-historical``, ``price-target-news``,
  ``analyst-estimates`` and every options endpoint are named in
  :data:`FORBIDDEN_ENDPOINTS` and refused before a call is made;
* the fetch stage refuses to run without ``--execute-fetch``;
* the plan is printed and capped before the first call —
  :data:`HARD_CALL_CAP` is 3,800 and one full pass over the frozen M1 pool is
  one call per unique ticker (~3,640);
* every response is written to a replay-owned cache, so the analysis stages
  never need the network again.

Point-in-time rules, also enforced:

* an action is usable only from the FIRST TRADING SESSION STRICTLY AFTER its
  event date. ``/stable/grades`` carries no time-of-day field, so an action
  dated D is treated as unknown on D;
* every trailing-window feature counts only actions usable on or before the
  scan date, and :func:`_assert_no_future_actions` re-checks a sample of rows
  against the raw feed and fails the stage if any feature could have used a
  future action;
* forward returns are labels only. No rule reads one.

Known limitation, stated up front: the feed is served as it stands today. An
action's DATE is an event date and is not restated, which is why this endpoint
was chosen, but a provider that silently back-fills or re-labels historical
grade rows would inject contamination this study cannot detect from a current
pull. That risk is disclosed in every artifact rather than assumed away.

Writes only ``cache/research/m1_analyst_actions_*`` and
``logs/m1_analyst_actions_latest.txt``. Changes no scanner, score, gate,
threshold, HC/EO rule, routing decision, dashboard artifact, MCP artifact, cron
entry or ledger, emits no trade signal, and cannot emit a live verdict token.

Sub-stages::

    plan     resolve the frozen M1 ticker list, print the call plan; 0 calls
    fetch    fetch /stable/grades per ticker (requires --execute-fetch)
    panel    build the point-in-time action panel from cache      (zero API)
    overlay  test the overlays inside the frozen M1 pool          (zero API)
    report   direct answers                                       (zero API)
"""

from __future__ import annotations

import argparse
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path

from research.backtests.common import (
    FetchNotAuthorised,
    LiveArtifactTripwire,
    REPLAY_WINDOW_END,
    REPLAY_WINDOW_START,
    ROOT,
    assert_conviction_verdict,
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

INTERMEDIATES_REL = "cache/research/m1_analyst_actions_intermediates"
TICKERS_REL = f"{INTERMEDIATES_REL}/m1_tickers.json"
RAW_REL = f"{INTERMEDIATES_REL}/grades_raw.jsonl"
FETCH_META_REL = f"{INTERMEDIATES_REL}/fetch_meta.json"
OUTCOMES_REL = f"{INTERMEDIATES_REL}/fetch_outcomes.json"
PANEL_REL = f"{INTERMEDIATES_REL}/analyst_action_panel.parquet"
UNIVERSE_BASE_REL = f"{INTERMEDIATES_REL}/universe_baseline.parquet"
PANEL_META_REL = "cache/research/m1_analyst_actions_panel_meta.json"
OVERLAY_REL = "cache/research/m1_analyst_actions_overlays.json"
LATEST_REL = "cache/research/m1_analyst_actions_latest.json"
REPORT_TXT_REL = "logs/m1_analyst_actions_latest.txt"

# ── provider policy ─────────────────────────────────────────────────────────

APPROVED_ENDPOINT = "/grades"
FORBIDDEN_ENDPOINTS: tuple[str, ...] = (
    "/grades-historical",
    "/price-target-news",
    "/price-target-summary",
    "/price-target-latest-news",
    "/analyst-estimates",
    "/options-chain",
    "/options",
)
HARD_CALL_CAP = 3800

# Actions must start before the replay window so the first scan dates have a
# full 126-session lookback. Two years of lead-in, which is free: one call per
# ticker returns the ticker's whole action history regardless of range.
ACTION_HISTORY_FLOOR = "2020-01-01"

# Trailing windows, in TRADING SESSIONS (not calendar days), taken from SPY's
# own session calendar so 63 == one quarter and 126 == one half-year the same
# way every other feature in this harness counts them.
WINDOWS: tuple[int, ...] = (7, 30, 63, 126)
PRIMARY_W = 63


class ForbiddenEndpoint(RuntimeError):
    """Raised when a stage tries to touch an endpoint outside the approval."""


def assert_endpoint_allowed(path: str) -> str:
    """Guard every provider path on its way to the client."""
    p = str(path)
    for bad in FORBIDDEN_ENDPOINTS:
        if p.rstrip("/") == bad or p.startswith(bad + "?"):
            raise ForbiddenEndpoint(
                f"{p} is outside this study's provider approval; only "
                f"{APPROVED_ENDPOINT} may be called")
    if p.rstrip("/") != APPROVED_ENDPOINT:
        raise ForbiddenEndpoint(
            f"{p} is not the approved endpoint {APPROVED_ENDPOINT}")
    return p


# ── grade vocabulary ────────────────────────────────────────────────────────
#
# Rating strings are free text from dozens of houses. They are mapped onto a
# 1-5 ordinal so that "Neutral -> Buy" and "Sector Perform -> Outperform" count
# as the same event. Anything unmapped is carried as UNKNOWN and reported as a
# coverage number rather than guessed at, because a wrong guess here would
# manufacture upgrades that never happened.

GRADE_ORDINALS: dict[str, int] = {}


def _register(ordinal: int, *names: str) -> None:
    for n in names:
        GRADE_ORDINALS[n] = ordinal


_register(5, "strong buy", "conviction buy", "top pick", "best idea",
          "double upgrade", "strong-buy")
_register(4, "buy", "outperform", "overweight", "accumulate", "add", "positive",
          "market outperform", "sector outperform", "outperformer", "long-term buy",
          "speculative buy", "above average", "action list buy", "focus list",
          "trading buy", "moderate buy", "over weight", "out perform",
          "sector outperformer", "attractive", "gradually accumulate")
_register(3, "hold", "neutral", "market perform", "equal weight", "equal-weight",
          "sector perform", "in-line", "in line", "peer perform", "perform",
          "sector weight", "average", "market weight", "mixed", "fair value",
          "sector performer", "equalweight", "market performer", "hold neutral")
_register(2, "underperform", "underweight", "reduce", "sector underperform",
          "negative", "below average", "market underperform", "moderate sell",
          "under perform", "under weight", "sector underperformer", "unattractive",
          "trading sell", "weak hold")
_register(1, "sell", "strong sell", "strong-sell", "conviction sell")

BULLISH_ORDINAL_FLOOR = 4          # "buy or better"


def grade_ordinal(raw) -> int | None:
    """Map a free-text grade onto 1 (strong sell) - 5 (strong buy), or None."""
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if not s or s in ("n/a", "na", "none", "-", "unrated", "not rated", "suspended"):
        return None
    s = s.replace("_", " ").replace("/", " ").strip()
    s = " ".join(s.split())
    if s in GRADE_ORDINALS:
        return GRADE_ORDINALS[s]
    # Tolerate decorations like "Buy (initiation)" or "Overweight - Top Pick"
    for sep in (" (", " - ", ", ", ": "):
        if sep in s:
            head = s.split(sep)[0].strip()
            if head in GRADE_ORDINALS:
                return GRADE_ORDINALS[head]
    return None


def classify_action(prev_ord: int | None, new_ord: int | None,
                    provider_action) -> str:
    """One of upgrade / downgrade / maintain / initiation / unknown.

    Derived from the ordinal PAIR, not from the provider's ``action`` string:
    the point of this study is to separate real rating changes from the
    maintain/reiterate noise that dominates the feed, and the ordinal pair is
    the only field that can do that without trusting a label.
    """
    if prev_ord is not None and new_ord is not None:
        if new_ord > prev_ord:
            return "upgrade"
        if new_ord < prev_ord:
            return "downgrade"
        return "maintain"
    if new_ord is not None and prev_ord is None:
        return "initiation"
    return "unknown"


# ── the frozen M1 ticker list ───────────────────────────────────────────────


def build_m1_tickers(root: Path, args) -> dict:
    """Resolve the frozen M1 pool and cache its unique ticker list.

    The pool is built the canonical way — the tournament's own pool builder,
    M1's own mask — so the fetch universe cannot drift from the pool the
    overlays are later tested in.
    """
    from research.backtests.alpha_tournament import _build_pools
    from research.backtests.m1_conviction_layer import m1_pool

    pools = _build_pools(root, args)
    pool = m1_pool(pools)
    tickers = sorted(set(str(t) for t in pool["ticker"].unique()))
    payload = {
        "kind": "M1_ANALYST_ACTIONS_TICKERS",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pool_rows": int(len(pool)),
        "scan_dates": int(pool["scan_date"].nunique()),
        "window": [str(pool["scan_date"].min()), str(pool["scan_date"].max())],
        "tickers": tickers,
        "n_tickers": len(tickers),
    }
    write_replay_json(root / TICKERS_REL, payload, root=root)
    return payload


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text()) if path.exists() else {}


def _load_outcomes(root: Path) -> dict:
    """The per-ticker outcome ledger from earlier passes, if there is one.

    Tolerates a ledger written before ``ok_tickers`` was recorded: the ok set is
    then derived from the planned list minus the failures, so a completion pass
    cannot mistake an already-fetched ticker for a failed one.
    """
    out = _load_json(root / OUTCOMES_REL)
    if out and not out.get("ok_tickers"):
        planned = (_load_json(root / TICKERS_REL).get("tickers") or [])
        out["ok_tickers"] = sorted(set(planned) - set(out.get("failed_tickers") or []))
    return out


def load_m1_tickers(root: Path) -> dict:
    f = root / TICKERS_REL
    if not f.exists():
        raise SystemExit(f"missing {TICKERS_REL} — run the plan stage first")
    return json.loads(f.read_text())


def plan_stage(args) -> int:
    """Resolve the ticker list and print the fetch plan. Makes no calls."""
    root = Path(args.root)
    tw = LiveArtifactTripwire.snapshot(root)
    f = root / TICKERS_REL
    if f.exists() and not args.rebuild_tickers:
        payload = json.loads(f.read_text())
    else:
        payload = build_m1_tickers(root, args)
    n = int(payload["n_tickers"])
    plan = {
        "kind": "M1_ANALYST_ACTIONS_FETCH_PLAN",
        "endpoint": f"{APPROVED_ENDPOINT} (per-ticker, full action history per call)",
        "forbidden_endpoints": list(FORBIDDEN_ENDPOINTS),
        "pool": "frozen M1 12-1 momentum pool",
        "pool_rows": payload["pool_rows"],
        "scan_dates": payload["scan_dates"],
        "planned_calls": n,
        "hard_cap": HARD_CALL_CAP,
        "fields_wanted": ["symbol", "date", "gradingCompany", "previousGrade",
                          "newGrade", "action"],
        "history_floor_kept": ACTION_HISTORY_FLOOR,
        "destination": RAW_REL,
        "calls_made_by_this_stage": 0,
    }
    print(json.dumps({k: v for k, v in plan.items()}, indent=1))
    print(tw.report())
    tw.assert_clean()
    if n > HARD_CALL_CAP:
        print(f"REFUSED: {n:,} planned calls exceed the {HARD_CALL_CAP:,} cap")
        return 3
    return 0


def fetch_stage(args) -> int:
    """Fetch /stable/grades per M1 ticker. Requires --execute-fetch; capped."""
    import os
    from concurrent.futures import ThreadPoolExecutor
    from threading import Lock

    root = Path(args.root)
    tw = LiveArtifactTripwire.snapshot(root)
    payload = load_m1_tickers(root)
    prior = _load_outcomes(root)
    if args.only_failed:
        # A completion pass: re-fetch ONLY the tickers whose call never returned,
        # and APPEND to the raw feed. Truncating it would throw away the rows the
        # first pass already paid for.
        tickers = list(prior.get("failed_tickers") or [])
        if not tickers:
            print("nothing to complete: no failed tickers recorded")
            return 0
    else:
        tickers = list(payload["tickers"])
    if args.limit:
        tickers = tickers[: args.limit]
    planned = len(tickers)
    require_execute_fetch(args, planned_calls=planned, what="analyst grades history")
    cap = min(args.max_calls or HARD_CALL_CAP, HARD_CALL_CAP)
    enforce_call_cap(planned, cap, what="analyst grades history")

    cred = os.environ.get("SNIPER_ENV_PATH", "/home/gem/secure/trading.env")
    if Path(cred).exists():
        from dotenv import load_dotenv

        load_dotenv(cred, override=True)
    else:
        raise FetchNotAuthorised(
            f"credential file not found at {cred}; set SNIPER_ENV_PATH. "
            "No calls were made.")

    from core.fmp_client import get_fmp

    prior_meta = _load_json(root / FETCH_META_REL)
    client = get_fmp()
    endpoint = assert_endpoint_allowed(APPROVED_ENDPOINT)
    keep_from = ACTION_HISTORY_FLOOR

    lock = Lock()
    state = {"calls": 0, "rows": 0}
    errors: list[dict] = []
    ok_syms: list[str] = []
    out_path = root / RAW_REL
    assert_replay_write_path(out_path, root=root)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fh = out_path.open("a" if args.only_failed else "w")
    t0 = time.time()

    def one(sym: str) -> None:
        # A 429 is a served response: it costs a call and it costs the ticker.
        # Retries are budgeted like any other call so a backoff loop can never
        # walk past the operator's cap.
        data = None
        last_err = None
        for attempt in range(args.retries + 1):
            with lock:
                if state["calls"] >= cap:
                    return
                state["calls"] += 1
                n_calls = state["calls"]
            try:
                data = client._get(endpoint, params={"symbol": sym, "limit": 1000})
                last_err = None
                break
            except Exception as e:
                last_err = f"{type(e).__name__}: {e.__class__.__name__}"
                status = getattr(getattr(e, "response", None), "status_code", None)
                if status is not None:
                    last_err = f"HTTP {status}"
                if attempt < args.retries:
                    time.sleep(args.backoff * (2 ** attempt))
        if data is None:
            with lock:
                errors.append({"symbol": sym, "error": last_err})
            return
        rows = data if isinstance(data, list) else []
        slim = []
        for r in rows:
            d = str(r.get("date") or "")[:10]
            if not d or d < keep_from:
                continue
            slim.append({"symbol": sym, "date": d,
                         "firm": r.get("gradingCompany"),
                         "prev": r.get("previousGrade"),
                         "new": r.get("newGrade"),
                         "action": r.get("action")})
        with lock:
            for s in slim:
                fh.write(json.dumps(s) + "\n")
            state["rows"] += len(slim)
            ok_syms.append(sym)
            if n_calls % 250 == 0:
                print(f"  [{n_calls}/{planned}] {state['rows']:,} rows · "
                      f"{time.time() - t0:.0f}s", flush=True)

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        list(ex.map(one, tickers))
    fh.close()

    all_planned = set(payload["tickers"])
    prior_ok = set(prior.get("ok_tickers") or [])
    if args.only_failed:
        ok_all = sorted((prior_ok | set(ok_syms)) & all_planned)
    else:
        ok_all = sorted(set(ok_syms))
    failed = sorted(all_planned - set(ok_all))
    outcomes = {
        "kind": "M1_ANALYST_ACTIONS_FETCH_OUTCOMES",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "passes": int(prior.get("passes", 1 if prior else 0)) + 1,
        "n_planned": len(all_planned), "n_ok": len(ok_all), "n_failed": len(failed),
        "this_pass": {"attempted": len(tickers), "returned": len(ok_syms),
                      "calls": state["calls"], "only_failed": bool(args.only_failed)},
        "reason": "tickers whose /grades call did not return a response",
        "ok_tickers": ok_all,
        "failed_tickers": failed,
    }
    write_replay_json(root / OUTCOMES_REL, outcomes, root=root)

    meta = {
        "kind": "M1_ANALYST_ACTIONS_FETCH_META",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "endpoint": endpoint,
        "calls_made": state["calls"],
        "planned_calls": planned,
        "hard_cap": HARD_CALL_CAP,
        "cap_applied": cap,
        "tickers": planned,
        "rows_kept": state["rows"],
        "history_floor_kept": keep_from,
        "tickers_ok": len(ok_all),
        "tickers_failed": len(failed),
        "tickers_ok_this_pass": len(ok_syms),
        "only_failed_pass": bool(args.only_failed),
        "cumulative_calls_all_passes": int(prior_meta.get("cumulative_calls_all_passes",
                                                          prior_meta.get("calls_made", 0)))
        + state["calls"],
        "errors": errors[:200],
        "error_count": len(errors),
        "outcomes_artifact": OUTCOMES_REL,
        "elapsed_sec": round(time.time() - t0, 1),
        **provenance_flags(),
    }
    assert_provenance(meta)
    write_replay_json(root / FETCH_META_REL, meta, root=root)
    print(tw.report())
    tw.assert_clean()
    print(f"\nfetched {state['rows']:,} action rows for {planned:,} tickers in "
          f"{state['calls']:,} calls ({len(errors)} errors) -> {RAW_REL}")
    return 0


# ── point-in-time action panel ──────────────────────────────────────────────


def load_sessions(root: Path):
    """The session calendar, from SPY's own replay series."""
    import pandas as pd

    f = root / "cache/replay_prices/SPY.parquet"
    if not f.exists():
        raise SystemExit("missing cache/replay_prices/SPY.parquet — the replay "
                         "price cache is required for the session calendar")
    return pd.DatetimeIndex(pd.read_parquet(f).sort_index().index)


def load_raw_actions(root: Path):
    """Deduplicated analyst actions with ordinals and a derived class."""
    import pandas as pd

    f = root / RAW_REL
    if not f.exists():
        raise SystemExit(f"missing {RAW_REL} — run the fetch stage first")
    rows = [json.loads(l) for l in f.read_text().splitlines() if l.strip()]
    df = pd.DataFrame(rows)
    if df.empty:
        raise SystemExit(f"{RAW_REL} is empty")
    df["symbol"] = df["symbol"].astype(str).str.upper()
    df["date"] = pd.to_datetime(df["date"])
    df["firm"] = df["firm"].fillna("UNKNOWN_FIRM").astype(str)
    df["prev_ord"] = df["prev"].map(grade_ordinal)
    df["new_ord"] = df["new"].map(grade_ordinal)
    df["klass"] = [classify_action(p, n, a)
                   for p, n, a in zip(df["prev_ord"], df["new_ord"], df["action"])]
    # The same action is occasionally served twice; a duplicate would inflate
    # both the count features and the breadth features.
    df = df.drop_duplicates(["symbol", "date", "firm", "prev", "new"])
    return df.sort_values(["symbol", "date"]).reset_index(drop=True)


FEATURE_COLS: tuple[str, ...] = tuple(
    [f"act_{k}_{w}" for w in WINDOWS
     for k in ("up", "down", "maintain", "init", "new_bullish", "net_up")]
    + ["act_mag_sum_63", "act_mag_mean_63", "act_up_firms_63", "act_up_firms_126",
       "act_down_firms_63", "act_total_63", "act_downgrade_pressure_63",
       "act_sessions_since_last", "act_sessions_since_last_change",
       "act_firms_126", "act_prior_count"]
)


def compute_action_features(pool, acts, sessions):
    """Trailing-window action features, as-of every scan date.

    An action dated D is usable from the first session STRICTLY AFTER D. Every
    window is counted in trading sessions, over actions whose usable session is
    in ``(scan_session - W, scan_session]``. Nothing after the scan date can
    enter a feature by construction: the search bound IS the scan session.
    """
    import numpy as np
    import pandas as pd

    sess = np.asarray(sessions, dtype="datetime64[ns]")
    a_dates = acts["date"].to_numpy("datetime64[ns]")
    usable_pos = np.searchsorted(sess, a_dates, side="right")
    keep = usable_pos < len(sess)
    acts = acts[keep].copy()
    acts["usable_sess"] = usable_pos[keep].astype(np.int64)
    acts["usable_date"] = sess[usable_pos[keep]]

    pool = pool.reset_index(drop=True).copy()
    scan_dates = pool["scan_date"].to_numpy("datetime64[ns]")
    scan_sess = np.searchsorted(sess, scan_dates, side="left")
    if not np.all(sess[np.clip(scan_sess, 0, len(sess) - 1)] == scan_dates):
        raise AssertionError("a scan date is not a trading session in the calendar")
    pool["_scan_sess"] = scan_sess.astype(np.int64)

    feats = {c: np.full(len(pool), np.nan, dtype=np.float64) for c in FEATURE_COLS}
    rows_by_ticker: dict[str, np.ndarray] = {
        t: np.asarray(g, dtype=np.int64)
        for t, g in pool.groupby("ticker", observed=True).indices.items()
    }
    acts_by_ticker = {t: g for t, g in acts.groupby("symbol", sort=False)}

    def _c(x):
        """0-prefixed cumulative sum, so a window count is cum[hi] - cum[lo]."""
        return np.concatenate(([0.0], np.cumsum(np.asarray(x, dtype=np.float64))))

    for tkr, ridx in rows_by_ticker.items():
        g = acts_by_ticker.get(str(tkr))
        s = pool["_scan_sess"].to_numpy()[ridx]
        if g is None or g.empty:
            for w in WINDOWS:
                for k in ("up", "down", "maintain", "init", "new_bullish", "net_up"):
                    feats[f"act_{k}_{w}"][ridx] = 0.0
            for c in ("act_mag_sum_63", "act_up_firms_63", "act_up_firms_126",
                      "act_down_firms_63", "act_total_63", "act_firms_126",
                      "act_prior_count"):
                feats[c][ridx] = 0.0
            continue
        g = g.sort_values("usable_sess")
        u = g["usable_sess"].to_numpy(np.int64)
        klass = g["klass"].to_numpy()
        up = klass == "upgrade"
        down = klass == "downgrade"
        maint = klass == "maintain"
        init = klass == "initiation"
        new_ord = pd.to_numeric(g["new_ord"], errors="coerce").to_numpy(np.float64)
        prev_ord = pd.to_numeric(g["prev_ord"], errors="coerce").to_numpy(np.float64)
        newbull = (new_ord >= BULLISH_ORDINAL_FLOOR) & (
            np.isnan(prev_ord) | (prev_ord < BULLISH_ORDINAL_FLOOR))
        mag = np.where(np.isnan(new_ord) | np.isnan(prev_ord), 0.0, new_ord - prev_ord)
        changed = up | down
        firm_codes = pd.factorize(g["firm"])[0]

        c_up, c_down, c_mai = _c(up), _c(down), _c(maint)
        c_init, c_nb, c_mag = _c(init), _c(newbull), _c(mag)
        c_known = _c(~(np.isnan(new_ord) | np.isnan(prev_ord)))

        hi = np.searchsorted(u, s, side="right")
        for w in WINDOWS:
            lo = np.searchsorted(u, s - w, side="right")
            u_w = c_up[hi] - c_up[lo]
            d_w = c_down[hi] - c_down[lo]
            feats[f"act_up_{w}"][ridx] = u_w
            feats[f"act_down_{w}"][ridx] = d_w
            feats[f"act_maintain_{w}"][ridx] = c_mai[hi] - c_mai[lo]
            feats[f"act_init_{w}"][ridx] = c_init[hi] - c_init[lo]
            feats[f"act_new_bullish_{w}"][ridx] = c_nb[hi] - c_nb[lo]
            feats[f"act_net_up_{w}"][ridx] = u_w - d_w
            if w == PRIMARY_W:
                lo63, hi63 = lo, hi
                mag_sum = c_mag[hi] - c_mag[lo]
                known = c_known[hi] - c_known[lo]
                feats["act_mag_sum_63"][ridx] = mag_sum
                with np.errstate(invalid="ignore", divide="ignore"):
                    feats["act_mag_mean_63"][ridx] = np.where(
                        known > 0, mag_sum / np.maximum(known, 1), np.nan)
                    tot = (u_w + d_w + (c_mai[hi] - c_mai[lo])
                           + (c_init[hi] - c_init[lo]))
                    feats["act_total_63"][ridx] = tot
                    feats["act_downgrade_pressure_63"][ridx] = np.where(
                        (u_w + d_w) > 0, d_w / np.maximum(u_w + d_w, 1), np.nan)

        feats["act_prior_count"][ridx] = hi.astype(np.float64)
        last_age = np.where(hi > 0, s - u[np.clip(hi - 1, 0, len(u) - 1)], np.nan)
        feats["act_sessions_since_last"][ridx] = last_age
        if changed.any():
            u_ch = u[changed]
            hi_ch = np.searchsorted(u_ch, s, side="right")
            feats["act_sessions_since_last_change"][ridx] = np.where(
                hi_ch > 0, s - u_ch[np.clip(hi_ch - 1, 0, len(u_ch) - 1)], np.nan)

        # Breadth: DISTINCT houses acting in the window. Two upgrades from one
        # house is one opinion; two houses is agreement, which is the thing the
        # "repeated upgrades" hypothesis is actually about.
        for name, sel, w in (("act_up_firms_63", up, 63),
                             ("act_up_firms_126", up, 126),
                             ("act_down_firms_63", down, 63),
                             ("act_firms_126", np.ones(len(u), dtype=bool), 126)):
            if not sel.any():
                feats[name][ridx] = 0.0
                continue
            u_s, f_s = u[sel], firm_codes[sel]
            lo_s = np.searchsorted(u_s, s - w, side="right")
            hi_s = np.searchsorted(u_s, s, side="right")
            feats[name][ridx] = [
                float(len(np.unique(f_s[a:b]))) if b > a else 0.0
                for a, b in zip(lo_s, hi_s)]

    for c, v in feats.items():
        pool[c] = v
    pool["act_any_prior"] = pool["act_prior_count"].fillna(0) > 0
    pool["act_none_126"] = (pool["act_up_126"].fillna(0) + pool["act_down_126"].fillna(0)
                            + pool["act_maintain_126"].fillna(0)
                            + pool["act_init_126"].fillna(0)) == 0
    pool["act_maintain_only_63"] = ((pool["act_maintain_63"].fillna(0) > 0)
                                    & (pool["act_up_63"].fillna(0) == 0)
                                    & (pool["act_down_63"].fillna(0) == 0))
    return pool, acts


def _assert_no_future_actions(panel, acts, n_checks: int = 400, seed: int = 5) -> dict:
    """Brute-force recount a sample of rows straight from the raw feed.

    Two failures are checked for, both fatal: an action usable only AFTER the
    scan date contributing to a feature, and a window count that disagrees with
    a direct recount.
    """
    import numpy as np

    rng = np.random.default_rng(seed)
    have = panel[panel["act_prior_count"].fillna(0) > 0]
    if have.empty:
        return {"rows_checked": 0}
    sample = have.iloc[rng.choice(len(have), size=min(n_checks, len(have)),
                                  replace=False)]
    by_sym = {s: g for s, g in acts.groupby("symbol", sort=False)}
    checked = 0
    for _, row in sample.iterrows():
        g = by_sym.get(str(row["ticker"]))
        if g is None:
            continue
        s = int(row["_scan_sess"])
        w = g[(g["usable_sess"] <= s) & (g["usable_sess"] > s - PRIMARY_W)]
        if (w["usable_date"] > row["scan_date"]).any():
            raise AssertionError(
                f"lookahead: {row['ticker']} on {row['scan_date']} counted an "
                "action that was not usable yet")
        exp_up = float((w["klass"] == "upgrade").sum())
        exp_down = float((w["klass"] == "downgrade").sum())
        if (float(row["act_up_63"]) != exp_up
                or float(row["act_down_63"]) != exp_down):
            raise AssertionError(
                f"window recount mismatch for {row['ticker']} on {row['scan_date']}: "
                f"panel {row['act_up_63']}/{row['act_down_63']} vs recount "
                f"{exp_up}/{exp_down}")
        checked += 1
    return {"rows_checked": checked}


def load_failed_tickers(root: Path) -> set[str]:
    """Tickers whose ``/grades`` call never returned a response."""
    f = root / OUTCOMES_REL
    if not f.exists():
        return set()
    return set(json.loads(f.read_text()).get("failed_tickers") or [])


def _universe_baseline(full, horizons):
    """Same-date universe mean/median per horizon — the third control."""
    import pandas as pd

    out = {}
    for h in horizons:
        col = f"fwd_{h}d"
        g = full.groupby("scan_date")[col]
        out[f"uni_mean_{h}d"] = g.mean()
        out[f"uni_median_{h}d"] = g.median()
    base = pd.DataFrame(out).reset_index()
    base["uni_rows"] = full.groupby("scan_date").size().to_numpy()
    return base


def panel_stage(args) -> int:
    import numpy as np
    import pandas as pd
    from research.backtests.alpha_tournament import HORIZONS, _build_pools
    from research.backtests.m1_conviction_layer import label_within_pool, m1_pool

    root = Path(args.root)
    tw = LiveArtifactTripwire.snapshot(root)
    pools = _build_pools(root, args)
    pool = label_within_pool(m1_pool(pools))
    base = _universe_baseline(pools["full"], HORIZONS)
    b_out = root / UNIVERSE_BASE_REL
    assert_replay_write_path(b_out, root=root)
    b_out.parent.mkdir(parents=True, exist_ok=True)
    base.to_parquet(b_out, index=False, compression="zstd")
    del pools

    sessions = load_sessions(root)
    acts = load_raw_actions(root)
    n0 = len(pool)
    panel, acts_u = compute_action_features(pool, acts, sessions)
    assert len(panel) == n0, "the action join must not change the pool size"
    audit = _assert_no_future_actions(panel, acts_u)

    # A ticker whose fetch never returned is NOT a ticker without analysts.
    # Conflating the two would poison the no-coverage control with the study's
    # own plumbing, so those rows are flagged and held out of the overlays.
    failed = load_failed_tickers(root)
    panel["act_data_missing"] = panel["ticker"].astype(str).isin(failed)
    if failed:
        miss = panel["act_data_missing"].to_numpy()
        for c in FEATURE_COLS:
            panel.loc[miss, c] = float("nan")
        for c in ("act_any_prior", "act_none_126", "act_maintain_only_63"):
            panel[c] = panel[c].astype("boolean")
            panel.loc[miss, c] = pd.NA

    # Earnings tags, if the surprise study's panel is present. Read-only, and
    # a left join so a missing tag never shrinks the pool.
    surprise_cols: list[str] = []
    sp = root / "cache/research/m1_surprise_intermediates/surprise_panel.parquet"
    if sp.exists():
        want = ["ticker", "scan_date", "eps_beat", "rev_beat", "days_since_earnings",
                "eps_surprise_vs_price_pct"]
        s = pd.read_parquet(sp, columns=want)
        s["ticker"] = s["ticker"].astype(str)
        s["scan_date"] = s["scan_date"].astype("datetime64[ns]")
        panel["ticker"] = panel["ticker"].astype(str)
        panel["scan_date"] = panel["scan_date"].astype("datetime64[ns]")
        panel = panel.merge(s, on=["ticker", "scan_date"], how="left")
        assert len(panel) == n0, "the surprise join must not change the pool size"
        surprise_cols = [c for c in want if c not in ("ticker", "scan_date")]

    out = root / PANEL_REL
    assert_replay_write_path(out, root=root)
    out.parent.mkdir(parents=True, exist_ok=True)
    slim = panel.copy()
    for c, dt in slim.dtypes.items():
        if dt == "float64":
            slim[c] = slim[c].astype("float32")
    slim["ticker"] = slim["ticker"].astype("category")
    slim.to_parquet(out, index=False, compression="zstd")

    ok = panel[~panel["act_data_missing"].fillna(False).astype(bool)]
    cov_klass = acts_u["klass"].value_counts(normalize=True).mul(100).round(1).to_dict()
    prov_agree = None
    if "action" in acts_u.columns:
        pa = acts_u["action"].astype(str).str.lower()
        agree = ((pa.str.contains("upgrade") & (acts_u["klass"] == "upgrade"))
                 | (pa.str.contains("downgrade") & (acts_u["klass"] == "downgrade"))
                 | (pa.str.contains("maintain") & (acts_u["klass"] == "maintain"))
                 | (pa.str.contains("initial") & (acts_u["klass"] == "initiation")))
        prov_agree = round(float(agree.mean() * 100), 1)
    unmapped = float(acts_u["new_ord"].isna().mean() * 100)

    meta = {
        "kind": "M1_ANALYST_ACTIONS_PANEL_META",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pool_rows": int(len(panel)),
        "unique_tickers": int(panel["ticker"].nunique()),
        "scan_dates": int(panel["scan_date"].nunique()),
        "action_rows_usable": int(len(acts_u)),
        "point_in_time_rule": (
            "an action is usable from the first trading session STRICTLY AFTER "
            "its event date; /stable/grades has no time-of-day field, so an "
            "action dated D is unknown on D. Windows are trading sessions."),
        "windows_sessions": list(WINDOWS),
        "lookahead_audit": audit,
        "action_class_mix_pct": cov_klass,
        "provider_action_label_agreement_pct": prov_agree,
        "unmapped_new_grade_pct": round(unmapped, 1),
        "coverage": {
            "denominator": "rows whose ticker returned a response",
            "rows_with_a_response": int(len(ok)),
            "rows_with_any_prior_action_pct": round(
                float((ok["act_prior_count"].fillna(0) > 0).mean() * 100), 1),
            "rows_with_action_in_126_pct": round(
                float((~ok["act_none_126"].fillna(False).astype(bool)).mean() * 100), 1),
            "rows_with_upgrade_in_63_pct": round(
                float((ok["act_up_63"].fillna(0) > 0).mean() * 100), 1),
            "rows_with_downgrade_in_63_pct": round(
                float((ok["act_down_63"].fillna(0) > 0).mean() * 100), 1),
            "rows_with_two_plus_upgrade_firms_63_pct": round(
                float((ok["act_up_firms_63"].fillna(0) >= 2).mean() * 100), 1),
            "median_actions_in_126": float(
                (ok["act_up_126"].fillna(0) + ok["act_down_126"].fillna(0)
                 + ok["act_maintain_126"].fillna(0)
                 + ok["act_init_126"].fillna(0)).median()),
            "unique_tickers_with_any_action": int(
                ok[ok["act_prior_count"].fillna(0) > 0].ticker.nunique()),
        },
        "surprise_tags_joined": surprise_cols,
        "fetch_gap": {
            "tickers_without_a_response": len(failed),
            "pool_rows_flagged_missing": int(panel["act_data_missing"].sum()),
            "pool_rows_flagged_missing_pct": round(
                float(panel["act_data_missing"].mean() * 100), 1),
            "handling": "flagged act_data_missing, features nulled, held out of "
                        "every overlay — a failed call is not evidence that a "
                        "name has no analyst coverage",
        },
        "feed_provenance_warning": (
            "/stable/grades is served as it stands today. Each row is an EVENT "
            "with its own date, which is why this endpoint was chosen over the "
            "snapshot-only analyst-estimates, but a provider that back-fills or "
            "re-labels historical grade rows would inject contamination this "
            "study cannot detect from a current pull. The rating STRINGS are "
            "free text from dozens of houses; "
            f"{round(unmapped, 1)}% of newGrade values did not map onto the "
            "1-5 ordinal and are carried as unknown rather than guessed."),
        **provenance_flags(),
    }
    assert_provenance(meta)
    write_replay_json(root / PANEL_META_REL, meta, root=root)
    print(tw.report())
    tw.assert_clean()
    print(json.dumps(meta["coverage"], indent=1))
    print(f"action classes: {cov_klass}")
    print(f"lookahead audit: {audit['rows_checked']} rows recounted from the raw feed")
    return 0


# ── overlays (test inside frozen M1) ────────────────────────────────────────

from research.backtests.alpha_reconstruction_lab import (  # noqa: E402
    HOLDOUT_YEARS, TRAIN_YEARS, _cluster_ci)
from research.backtests.alpha_tournament import (  # noqa: E402
    HORIZONS, PRIMARY_H, _pct, _top_n, _z, asymmetry, selection_vs)
from research.backtests.m1_conviction_layer import (  # noqa: E402
    WALK_FORWARD_STEPS, _diff_vs, evaluate_list, random_control)

# The list sizes this study was approved to report. 200 is dropped: a 200-name
# list is not a conviction claim, and reporting it would let a thin overlay
# look useful on a list nobody would read.
LIST_SIZES: tuple[int, ...] = (10, 25, 50, 100)

# Pre-registered thresholds. Written before the overlays were scored, and
# deliberately the same bars the conviction and surprise studies used, so this
# study cannot pass on a bar the earlier ones would have failed.
PROMOTION = {
    "min_coverage_pct": 10.0,          # below this the feature is too sparse to judge
    "min_names_per_date_for_list": 25,  # a top-25 claim needs 25 names to pick from
    "needs_positive_vs_random_ci_at": (25, 50),
    "needs_holdout_positive": True,
    "needs_ticker_clustered_ci_above_zero": True,
    "min_walk_forward_positive_steps": 2,
    "avoid_filter_min_left_tail_cut_pct": 15.0,
    "avoid_filter_max_right_tail_cost_pct": 20.0,
}

# The first pass covered 56.3% of pool rows: 1,546 of 3,640 tickers were lost to
# provider rate-limit rejections, not to any property of the data. Its headline
# numbers are frozen here so the completed run can be compared against them
# honestly rather than from memory — every one of them was measured on the
# partial pool and NONE of them should be quoted as a result.
PARTIAL_PASS_REFERENCE: dict = {
    "recorded": "2026-09-07 run at 56.3% pool coverage (2,094 of 3,640 tickers)",
    "status": "SUPERSEDED — retained only for the coverage-sensitivity comparison",
    "pool_rows": 70421,
    "verdict_counts": {"INCONCLUSIVE": 9, "USEFUL_EVENT_ANNOTATION": 4,
                       "REJECTED_DATA_UNSAFE": 5},
    "overlays": {
        "positive_action_momentum": {"cov": 14.4, "per_date": 48,
                                     "vs_random_t50_train": 0.833,
                                     "vs_random_t50_holdout": 0.648,
                                     "subset_vs_pool": 1.089,
                                     "walk_forward": [0.123, 0.474]},
        "repeated_upgrades": {"cov": 4.4, "per_date": 14,
                              "vs_random_t50_train": 1.845,
                              "vs_random_t50_holdout": 2.812,
                              "subset_vs_pool": 2.224,
                              "walk_forward": [1.324, 1.973]},
        "upgrade_into_strong_momentum": {"cov": 3.2, "per_date": 11,
                                         "vs_random_t50_train": 6.437,
                                         "vs_random_t50_holdout": 9.287,
                                         "subset_vs_pool": 7.463,
                                         "walk_forward": [8.928, 6.704]},
        "new_bullish_coverage": {"cov": 15.1, "per_date": 51,
                                 "vs_random_t50_train": 1.22,
                                 "vs_random_t50_holdout": 0.849,
                                 "subset_vs_pool": 1.29,
                                 "walk_forward": [0.431, 1.668]},
        "upgrade_plus_revenue_surprise": {"cov": 10.9, "per_date": 37,
                                          "vs_random_t50_train": 0.337,
                                          "vs_random_t50_holdout": 1.107,
                                          "subset_vs_pool": 0.677,
                                          "walk_forward": [None, None]},
    },
    "cohorts_vs_pool": {"upgrade_63_alone": 1.034, "downgrade_63_alone": 0.925,
                        "maintain_only_63": -0.193, "no_action_126": -0.737,
                        "top_momentum_and_upgrade": 7.217},
}

FRESH_W = 30      # "recent action" window, in sessions
STRONG_MOM_PCT = 0.80


def _has(d, col: str):
    """A numeric column as float, or all-NaN when the join did not supply it."""
    import numpy as np
    import pandas as pd

    if col in d.columns:
        return pd.to_numeric(d[col], errors="coerce")
    return pd.Series(np.nan, index=d.index)


def _flag(d, col: str):
    """A plain boolean column, False where absent or null.

    Deliberately not ``.map({1.0: True, ...})``: parquet round-trips these
    columns as float, nullable boolean, or object depending on whether any NA
    survived the join, and a dict map silently returns all-False on one of
    those shapes. Coercing through fillna/astype behaves the same on all three.
    """
    import pandas as pd

    if col not in d.columns:
        return pd.Series(False, index=d.index)
    return d[col].fillna(False).astype(bool)


ACTION_OVERLAYS: list[dict] = [
    {"name": "m1_native_control", "kind": "ranker", "intent": "control",
     "hypothesis": "CONTROL — the frozen M1 pool ordered by its own 12-1 momentum. "
                   "Every other row is judged against this and against a random draw.",
     "mask": lambda d: d["price"].notna(),
     "rank": lambda d: _z(d, "mom_12_1")},
    {"name": "positive_action_momentum", "kind": "filter",
     "hypothesis": "net upgrades over the last quarter are positive — the direct "
                   "reading of analyst-action momentum",
     "mask": lambda d: _has(d, "act_net_up_63") > 0,
     "rank": lambda d: _z(d, "act_net_up_63") + _z(d, "act_mag_sum_63")},
    {"name": "repeated_upgrades", "kind": "filter",
     "hypothesis": "two or more DISTINCT houses upgraded inside the quarter — "
                   "agreement, not one analyst's opinion",
     "mask": lambda d: _has(d, "act_up_firms_63") >= 2,
     "rank": lambda d: _z(d, "act_up_firms_63") + _z(d, "act_mag_sum_63")},
    {"name": "fresh_upgrade_30d", "kind": "filter",
     "hypothesis": "an upgrade inside the last 30 sessions — the event is still live",
     "mask": lambda d: _has(d, "act_up_30") > 0,
     "rank": lambda d: _z(d, "act_up_30") + _z(d, "act_mag_sum_63")},
    {"name": "large_rating_improvement", "kind": "filter",
     "hypothesis": "magnitude, not direction: top-quintile summed grade improvement",
     "mask": lambda d: (_pct(d, "act_mag_sum_63") >= 0.80) & (_has(d, "act_up_63") > 0),
     "rank": lambda d: _z(d, "act_mag_sum_63")},
    {"name": "new_bullish_coverage", "kind": "filter",
     "hypothesis": "a house moved to buy-or-better from below it (or initiated "
                   "there) inside the quarter",
     "mask": lambda d: _has(d, "act_new_bullish_63") > 0,
     "rank": lambda d: _z(d, "act_new_bullish_63")},
    {"name": "no_downgrade_pressure", "kind": "filter",
     "hypothesis": "no downgrade in the quarter, and at least one house covering",
     "mask": lambda d: (_has(d, "act_down_63") == 0) & (_has(d, "act_firms_126") > 0),
     "rank": lambda d: _z(d, "mom_12_1")},
    {"name": "upgrade_plus_fundamental_quality", "kind": "filter",
     "hypothesis": "an upgrade on a profitable, cash-generative, non-diluting name",
     "mask": lambda d: ((_has(d, "act_up_63") > 0) & (_has(d, "profitable") == 1.0)
                        & (_has(d, "fcf_positive") == 1.0)
                        & (_has(d, "dilution_yoy_pct") < 5)),
     "rank": lambda d: _z(d, "act_net_up_63")},
    {"name": "upgrade_plus_revenue_surprise", "kind": "filter",
     "hypothesis": "an upgrade on a name whose last report beat on revenue",
     "mask": lambda d: (_has(d, "act_up_63") > 0) & _flag(d, "rev_beat"),
     "rank": lambda d: _z(d, "act_net_up_63")},
    {"name": "upgrade_after_earnings_surprise", "kind": "filter",
     "hypothesis": "the upgrade FOLLOWS a beat inside the same quarter — the "
                   "analyst is reacting to the print",
     "mask": lambda d: ((_has(d, "act_up_63") > 0) & _flag(d, "eps_beat")
                        & (_has(d, "days_since_earnings") <= 90)),
     "rank": lambda d: _z(d, "act_net_up_63")},
    {"name": "upgrade_into_strong_momentum", "kind": "filter",
     "hypothesis": "an upgrade on a name already in the pool's top momentum "
                   "quintile — analyst confirming the tape",
     "mask": lambda d: (_has(d, "act_up_63") > 0)
                       & (_pct(d, "mom_12_1") >= STRONG_MOM_PCT),
     "rank": lambda d: _z(d, "act_net_up_63") + _z(d, "mom_12_1")},
    {"name": "upgrade_not_exhaustion", "kind": "filter",
     "hypothesis": "an upgrade, held out of the volatility decile and the "
                   "most-extended decile where payoffs invert",
     "mask": lambda d: ((_has(d, "act_up_63") > 0) & (_pct(d, "atr14_pct") <= 0.80)
                        & (_pct(d, "dist_ma50_pct") <= 0.90)),
     "rank": lambda d: _z(d, "act_net_up_63")},
    {"name": "avoid_downgrade", "kind": "filter", "intent": "avoid",
     "hypothesis": "AVOID FILTER — drop names downgraded inside the quarter. A "
                   "name with no coverage is KEPT: absence of an analyst is not "
                   "a downgrade.",
     "mask": lambda d: ~(_has(d, "act_down_63") > 0),
     "rank": lambda d: _z(d, "mom_12_1")},
    {"name": "avoid_downgrade_despite_strength", "kind": "filter", "intent": "avoid",
     "hypothesis": "AVOID FILTER — drop only names downgraded WHILE in the pool's "
                   "top momentum quintile: the analyst disagreeing with a strong tape",
     "mask": lambda d: ~((_has(d, "act_down_63") > 0)
                         & (_pct(d, "mom_12_1") >= STRONG_MOM_PCT)),
     "rank": lambda d: _z(d, "mom_12_1")},
    {"name": "rank_by_net_upgrades", "kind": "ranker",
     "hypothesis": "CONTROL — re-order the WHOLE pool by net upgrades, no filter. "
                   "Tests ranking power directly, without a coverage screen.",
     "mask": lambda d: d["price"].notna(),
     "rank": lambda d: _z(d, "act_net_up_63") + _z(d, "act_mag_sum_63")},
    {"name": "maintain_noise_control", "kind": "filter", "intent": "control",
     "hypothesis": "CONTROL — covered names whose only actions in the quarter were "
                   "maintains. If these do as well as upgrades, the signal is "
                   "coverage, not the upgrade.",
     "mask": lambda d: _flag(d, "act_maintain_only_63"),
     "rank": lambda d: _z(d, "mom_12_1")},
    {"name": "no_analyst_action_control", "kind": "filter", "intent": "control",
     "hypothesis": "CONTROL — no analyst action at all in the last 126 sessions. "
                   "If these do as well, the data is adding nothing.",
     "mask": lambda d: _flag(d, "act_none_126"),
     "rank": lambda d: _z(d, "mom_12_1")},
    {"name": "stale_coverage_control", "kind": "filter", "intent": "control",
     "hypothesis": "CONTROL — covered at some point, but nothing for two quarters. "
                   "Separates 'no analyst' from 'analyst stopped caring'.",
     "mask": lambda d: (_has(d, "act_prior_count") > 0)
                       & (_has(d, "act_sessions_since_last") > 126),
     "rank": lambda d: _z(d, "mom_12_1")},
]


def _restrict_to_fetched(pool_all, base, args) -> tuple[object, dict]:
    """Drop rows whose ticker never returned a response, and show the cost.

    The held-out rows are a plumbing artifact, not a market fact, so the study
    has to demonstrate that removing them did not quietly change what M1 is:
    the restricted pool's own selection against the same-date universe is
    reported next to the full pool's.
    """
    if "act_data_missing" not in pool_all.columns:
        return pool_all, {"rows_dropped": 0, "rows_dropped_pct": 0.0,
                          "tickers_dropped": 0}
    miss = pool_all["act_data_missing"].fillna(False).astype(bool)
    pool = pool_all[~miss].copy()
    rep = {
        "rows_dropped": int(miss.sum()),
        "rows_dropped_pct": round(float(miss.mean() * 100), 1),
        "tickers_dropped": int(pool_all[miss].ticker.nunique()),
        "tickers_kept": int(pool.ticker.nunique()),
        "dates_kept": int(pool.scan_date.nunique()),
        "median_names_per_date_before": float(
            pool_all.groupby("scan_date").size().median()),
        "median_names_per_date_after": float(
            pool.groupby("scan_date").size().median()),
        "reason": "HTTP rejections during the single approved provider pass",
        "note": "a dropped row is a call that never returned, not a name without "
                "analyst coverage",
    }
    for label, frame in (("full_pool", pool_all), ("restricted_pool", pool)):
        rep[f"{label}_vs_universe_{PRIMARY_H}d"] = _selection_vs_universe(
            frame, base, PRIMARY_H, args)
        a = asymmetry(frame[f"fwd_{PRIMARY_H}d"]) or {}
        rep[f"{label}_mean_{PRIMARY_H}d"] = a.get("mean")
        rep[f"{label}_median_{PRIMARY_H}d"] = a.get("median")
    return pool, rep


def _selection_vs_universe(picks, base, h: int, args) -> dict | None:
    """Per-date picks mean minus the SAME-DATE UNIVERSE mean, with a date CI."""
    import numpy as np
    import pandas as pd

    if picks.empty or base is None or base.empty:
        return None
    col = f"fwd_{h}d"
    ucol = f"uni_mean_{h}d"
    if ucol not in base.columns:
        return None
    p = picks.groupby("scan_date")[col].mean()
    u = base.set_index("scan_date")[ucol]
    j = pd.DataFrame({"p": p, "u": u}).dropna()
    if len(j) < 10:
        return None
    d = (j["p"] - j["u"]).to_numpy()
    return {"mean_difference": round(float(d.mean()), 3),
            "date_cluster_ci": _cluster_ci(d, args.bootstrap, args.seed + 11),
            "dates_won_pct": round(float((d > 0).mean() * 100), 1),
            "n_dates": int(len(j))}


def _by_horizon(picks, pool, base, args) -> dict:
    """Every metric the brief asks for, at every approved horizon."""
    out = {}
    for h in HORIZONS:
        a = asymmetry(picks[f"fwd_{h}d"]) or {}
        sel = selection_vs(picks, pool, h, args.bootstrap, args.seed)
        out[f"{h}d"] = {
            "mean": a.get("mean"), "median": a.get("median"),
            "trim10": a.get("trim10"), "winsor5_95": a.get("winsor5_95"),
            "win_rate": a.get("win_rate"), "tail_ratio": a.get("tail_ratio"),
            "pct_up_50": a.get("pct_up_50"), "pct_down_25": a.get("pct_down_25"),
            "selection_vs_pool_mean": (sel or {}).get("selection_mean"),
            "date_cluster_ci_mean": (sel or {}).get("date_cluster_ci_mean"),
            "ticker_cluster_ci": (sel or {}).get("ticker_cluster_ci"),
            "selection_vs_universe": _selection_vs_universe(picks, base, h, args),
        }
    return out


def verdict_for(ov: dict, res: dict, coverage_pct: float) -> tuple[str, list[str]]:
    """The conviction ladder, applied with the pre-registered thresholds."""
    reasons: list[str] = []
    if ov.get("intent") == "control":
        # A control is reported, never promoted: calling a control an overlay
        # is how a study accidentally recommends its own null hypothesis.
        return assert_conviction_verdict("INCONCLUSIVE"), [
            "control row — reported for comparison, not eligible for a verdict"]
    if coverage_pct < PROMOTION["min_coverage_pct"]:
        return assert_conviction_verdict("REJECTED_DATA_UNSAFE"), [
            f"only {coverage_pct:.1f}% of pool rows carry this feature — below the "
            f"{PROMOTION['min_coverage_pct']:.0f}% floor"]

    tr = (res.get("train") or {})
    if not ((tr.get("top50") or {}).get("episodes")):
        return assert_conviction_verdict("INCONCLUSIVE"), ["no train episodes at top-50"]

    beats_random = False
    for n in PROMOTION["needs_positive_vs_random_ci_at"]:
        ci = ((tr.get(f"vs_random_top{n}") or {}).get("mean_ci") or [None, None])
        if ci[0] is not None and ci[0] > 0:
            beats_random = True
    ho = (res.get("holdout") or {})
    holds_out = any(((ho.get(f"vs_random_top{n}") or {}).get("mean_difference") or -1) > 0
                    for n in PROMOTION["needs_positive_vs_random_ci_at"])
    sel = (tr.get("top50") or {}).get("selection_vs_pool") or {}
    t_ci = sel.get("ticker_cluster_ci") or [None, None]
    per_name = t_ci[0] is not None and t_ci[0] > 0
    wf = res.get("walk_forward") or []
    wf_pos = sum(1 for w in wf if (w.get("mean_difference_vs_random") or -1) > 0)
    prof = res.get("tail_profile") or {}
    amplifies_both = bool(prof.get("amplifies_both_tails"))
    thin = ((res.get("coverage") or {}).get("median_names_per_date") or 0) < \
        PROMOTION["min_names_per_date_for_list"]

    if not beats_random:
        reasons.append("does not beat a random same-size M1 draw at top-25 or top-50 "
                       "with its CI clear of zero in train")
    if not holds_out:
        reasons.append("holdout difference vs random is not above zero")
    if not per_name:
        reasons.append(f"ticker-clustered CI {t_ci} does not clear zero — no per-name gain")
    if wf_pos < PROMOTION["min_walk_forward_positive_steps"]:
        reasons.append(f"positive in only {wf_pos} of {len(wf)} walk-forward steps")
    if amplifies_both:
        reasons.append(f"raises the +50% rate {prof.get('right_tail_change_pct')}% AND "
                       f"the -25% rate {prof.get('left_tail_change_pct')}% — liveness, "
                       "not conviction")
    if thin:
        reasons.append("fewer than 25 names on the median date — cannot fill a list")

    if ov.get("intent") == "avoid":
        left = prof.get("left_tail_change_pct")
        right = prof.get("right_tail_change_pct")
        if (left is not None and right is not None
                and left <= -PROMOTION["avoid_filter_min_left_tail_cut_pct"]
                and right >= -PROMOTION["avoid_filter_max_right_tail_cost_pct"]):
            return assert_conviction_verdict("USEFUL_AVOID_FILTER"), [
                f"cuts the -25% rate by {abs(left):.0f}% while costing "
                f"{abs(min(right, 0)):.0f}% of the +50% rate"]
        return assert_conviction_verdict("INCONCLUSIVE"), (
            ["did not reshape the tails favourably"] + reasons)

    if beats_random and holds_out and per_name and not amplifies_both and not thin:
        return assert_conviction_verdict("PROMISING_CONVICTION_OVERLAY"), [
            "beats a random M1 draw in train and holdout, improves the per-name "
            "reading, fills a list, and does not simply amplify both tails"]
    if beats_random and not holds_out:
        return assert_conviction_verdict("REJECTED_OVERFIT"), (
            ["beat random in train, not in holdout"] + reasons)
    subset = res.get("subset_vs_pool") or {}
    if (subset.get("mean_difference") or 0) > 0 and (
            (subset.get("mean_ci") or [None, None])[0] or -1) > 0:
        return assert_conviction_verdict("USEFUL_EVENT_ANNOTATION"), (
            [f"the tagged subset beats the rest of the pool by "
             f"{subset['mean_difference']}pp with CI {subset['mean_ci']}, but cannot "
             f"order the list"] + reasons)
    if ((sel.get("selection_mean") or 0) < 0
            and ((sel.get("date_cluster_ci_mean") or [0, 0])[1] or 0) < 0):
        return assert_conviction_verdict("REJECTED_NEGATIVE_SELECTION"), (
            ["negative selection against its own pool"] + reasons)
    return assert_conviction_verdict("INCONCLUSIVE"), reasons


def evaluate_overlay(ov: dict, pool, base, args) -> dict:
    """Score one overlay at every approved list size and horizon."""
    import pandas as pd

    try:
        mask = ov["mask"](pool).fillna(False).astype(bool)
    except Exception as e:
        return {"name": ov["name"],
                "verdict": assert_conviction_verdict("REJECTED_DATA_UNSAFE"),
                "verdict_reasons": [f"rule failed: {type(e).__name__}: {e}"]}
    sub = pool[mask]
    cov = 100.0 * len(sub) / max(len(pool), 1)
    res = {
        "name": ov["name"], "kind": ov["kind"], "intent": ov.get("intent", "alpha"),
        "hypothesis": ov["hypothesis"],
        "coverage": {
            "rows": int(len(sub)), "share_of_pool_pct": round(cov, 1),
            "median_names_per_date": (float(sub.groupby("scan_date").size().median())
                                      if not sub.empty else 0.0),
            "unique_tickers": int(sub.ticker.nunique()) if not sub.empty else 0,
            "dates_covered_pct": (round(100.0 * sub.scan_date.nunique()
                                        / max(pool.scan_date.nunique(), 1), 1)
                                  if not sub.empty else 0.0),
        },
    }
    if sub.empty:
        res["verdict"] = assert_conviction_verdict("INCONCLUSIVE")
        res["verdict_reasons"] = ["rule selected no rows"]
        return res

    rest = pool[~mask]
    a_sub = asymmetry(sub[f"fwd_{PRIMARY_H}d"])
    a_rest = asymmetry(rest[f"fwd_{PRIMARY_H}d"])
    if a_sub and a_rest and a_rest.get("pct_up_50") and a_rest.get("pct_down_25"):
        right = (a_sub["pct_up_50"] - a_rest["pct_up_50"]) / a_rest["pct_up_50"] * 100
        left = (a_sub["pct_down_25"] - a_rest["pct_down_25"]) / a_rest["pct_down_25"] * 100
        res["tail_profile"] = {
            "subset_pct_up_50": a_sub["pct_up_50"], "rest_pct_up_50": a_rest["pct_up_50"],
            "subset_pct_down_25": a_sub["pct_down_25"],
            "rest_pct_down_25": a_rest["pct_down_25"],
            "subset_tail_ratio": a_sub.get("tail_ratio"),
            "rest_tail_ratio": a_rest.get("tail_ratio"),
            "right_tail_change_pct": round(float(right), 1),
            "left_tail_change_pct": round(float(left), 1),
            "amplifies_both_tails": bool(right > 10 and left > 10),
        }
    res["subset_vs_pool"] = _diff_vs(sub, rest, args)
    res["subset_by_horizon"] = _by_horizon(sub, pool, base, args)

    rank = ov["rank"](pool)
    for split, years in (("train", TRAIN_YEARS), ("holdout", HOLDOUT_YEARS)):
        p_split = pool[pool.year.isin(years)]
        s_split = sub[sub.year.isin(years)]
        node = {"subset": evaluate_list(s_split, p_split, args)}
        for n in LIST_SIZES:
            picks = _top_n(s_split, rank, n)
            node[f"top{n}"] = evaluate_list(picks, p_split, args)
            node[f"vs_random_top{n}"] = _diff_vs(
                picks, random_control(p_split, n, args.seed + n), args)
            node[f"vs_m1_native_top{n}"] = _diff_vs(
                picks, _top_n(p_split, pool["mom_12_1"], n), args)
            if n in (25, 50):
                node[f"by_horizon_top{n}"] = _by_horizon(picks, p_split, base, args)
        res[split] = node

    res["walk_forward"] = []
    for train_y, test_y in WALK_FORWARD_STEPS:
        p_test = pool[pool.year == test_y]
        picks = _top_n(sub[sub.year == test_y], rank, 50)
        if picks.empty:
            continue
        d = _diff_vs(picks, random_control(p_test, 50, args.seed + test_y), args)
        a = asymmetry(picks[f"fwd_{PRIMARY_H}d"]) or {}
        res["walk_forward"].append({
            "rules_from": train_y, "tested_on": test_y, "episodes": int(len(picks)),
            "mean": a.get("mean"), "median": a.get("median"),
            "win_rate": a.get("win_rate"),
            "mean_difference_vs_random": (d or {}).get("mean_difference")})
    res["by_year"] = {
        str(int(y)): {
            "episodes": int((sub.year == y).sum()),
            "subset_mean": (asymmetry(sub[sub.year == y][f"fwd_{PRIMARY_H}d"])
                            or {}).get("mean"),
        } for y in sorted(pool.year.unique())}
    res["verdict"], res["verdict_reasons"] = verdict_for(ov, res, cov)
    return res


def _to_nullable_bool(s):
    """Float / object / nullable-boolean -> nullable boolean, NA preserved.

    The distinction matters: NA means "no data" (an unreported beat, a ticker
    whose fetch never returned) and must never collapse into False, which would
    read as "no upgrade" and quietly recruit missing data into a control.
    """
    import pandas as pd

    num = pd.to_numeric(s, errors="coerce").to_numpy(dtype=float)
    return pd.Series(pd.array([pd.NA if v != v else bool(v) for v in num],
                              dtype="boolean"), index=s.index)


def load_action_panel(root: Path):
    import pandas as pd

    f = root / PANEL_REL
    if not f.exists():
        raise SystemExit(f"missing {PANEL_REL} — run the panel stage first")
    pool = pd.read_parquet(f)
    pool["ticker"] = pool["ticker"].astype(str)
    for c in ("eps_beat", "rev_beat", "act_any_prior", "act_none_126",
              "act_maintain_only_63"):
        if c in pool.columns:
            pool[c] = _to_nullable_bool(pool[c])
    base = None
    b = root / UNIVERSE_BASE_REL
    if b.exists():
        base = pd.read_parquet(b)
        base["scan_date"] = base["scan_date"].astype("datetime64[ns]")
    return pool, base


def overlay_stage(args) -> int:
    root = Path(args.root)
    tw = LiveArtifactTripwire.snapshot(root)
    pool_all, base = load_action_panel(root)
    pool, restriction = _restrict_to_fetched(pool_all, base, args)
    print(f"fetch gap: {restriction['rows_dropped']:,} of {len(pool_all):,} pool rows "
          f"held out ({restriction['rows_dropped_pct']}%) — "
          f"{restriction['tickers_dropped']:,} tickers never returned a response",
          flush=True)
    print(f"M1 pool with analyst actions: {len(pool):,} rows · "
          f"{(pool['act_up_63'].fillna(0) > 0).mean() * 100:.1f}% carry an upgrade "
          f"in the last quarter", flush=True)

    results = []
    for ov in ACTION_OVERLAYS:
        t0 = time.time()
        r = evaluate_overlay(ov, pool, base, args)
        results.append(r)
        tr = (r.get("train") or {}).get("top50") or {}
        vr = ((r.get("train") or {}).get("vs_random_top50") or {})
        hr = ((r.get("holdout") or {}).get("vs_random_top50") or {})
        tp = r.get("tail_profile") or {}
        print(f"  {ov['name']:<34} cov={r['coverage']['share_of_pool_pct']:>5}% "
              f"n/date={r['coverage']['median_names_per_date']:>5.0f} "
              f"t50mean={str(tr.get('mean')):>8} vsR_tr={str(vr.get('mean_difference')):>7} "
              f"vsR_ho={str(hr.get('mean_difference')):>7} "
              f"tails={str(tp.get('right_tail_change_pct')):>6}/"
              f"{str(tp.get('left_tail_change_pct')):>6} -> {r.get('verdict')} "
              f"({time.time() - t0:.0f}s)", flush=True)

    out = {
        "kind": "M1_ANALYST_ACTIONS_OVERLAY_RESULTS",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "endpoint": APPROVED_ENDPOINT,
        "primary_horizon_days": PRIMARY_H,
        "horizons": list(HORIZONS),
        "list_sizes": list(LIST_SIZES),
        "pool_rows": int(len(pool)),
        "pool_rows_before_fetch_gap": int(len(pool_all)),
        "fetch_gap_restriction": restriction,
        "upgrade_coverage_pct": round(
            float((pool["act_up_63"].fillna(0) > 0).mean() * 100), 1),
        "pre_registered_thresholds": PROMOTION,
        "control_note": "every list is compared with (a) a RANDOM same-size draw "
                        "from the same M1 pool on the same dates, (b) the M1-native "
                        "top-N by 12-1 momentum, and (c) the same-date universe",
        "holdout_note": "2025 is reported as ALREADY-SPENT context, not clean "
                        "discovery: earlier studies in this programme used it.",
        "overlays": results,
        "verdict_counts": {},
        **provenance_flags(),
    }
    for r in results:
        v = r.get("verdict", "INCONCLUSIVE")
        out["verdict_counts"][v] = out["verdict_counts"].get(v, 0) + 1
    assert_provenance(out)
    write_replay_json(root / OVERLAY_REL, out, root=root)
    print(tw.report())
    tw.assert_clean()
    print("verdicts: " + json.dumps(out["verdict_counts"]))
    return 0


def attribution_test(pool, args) -> dict:
    """Whose result is it — the upgrade, the momentum, or the fundamentals?

    An upgrade does not arrive on a random name. It arrives on a name that has
    already gone up, and often on a profitable one. Reporting the bundle
    without decomposing it would credit analyst data for the pool's own
    momentum and for a fundamental screen the conviction study already had.
    """
    import pandas as pd

    up = (_has(pool, "act_up_63") > 0).fillna(False)
    down = (_has(pool, "act_down_63") > 0).fillna(False)
    beat = _flag(pool, "eps_beat")
    fq = ((_has(pool, "profitable") == 1.0) & (_has(pool, "fcf_positive") == 1.0)
          & (_has(pool, "dilution_yoy_pct") < 5)).fillna(False)
    # The momentum control. An upgrade lands on a name that has already run, so
    # the top-momentum cohort has to be priced in before any of the upgrade's
    # result can be credited to the analyst.
    strong = (_pct(pool, "mom_12_1") >= STRONG_MOM_PCT).fillna(False)
    cohorts = {
        "whole_pool": pd.Series(True, index=pool.index),
        "upgrade_63_alone": up,
        "downgrade_63_alone": down,
        "eps_beat_alone": beat,
        "upgrade_and_beat": up & beat,
        "upgrade_without_beat": up & ~beat,
        "beat_without_upgrade": beat & ~up,
        "fundamental_quality_alone": fq,
        "fundamental_quality_plus_upgrade": fq & up,
        "no_action_126": _flag(pool, "act_none_126"),
        "maintain_only_63": _flag(pool, "act_maintain_only_63"),
        "top_momentum_quintile_alone": strong,
        "top_momentum_and_upgrade": strong & up,
        "top_momentum_without_upgrade": strong & ~up,
        "upgrade_outside_top_momentum": up & ~strong,
    }
    out = {"cohorts": {}}
    for name, m in cohorts.items():
        g = pool[m.fillna(False).astype(bool)]
        if g.empty:
            out["cohorts"][name] = {"rows": 0}
            continue
        a = asymmetry(g[f"fwd_{PRIMARY_H}d"]) or {}
        d = _diff_vs(g, pool, args) or {}
        out["cohorts"][name] = {
            "rows": int(len(g)), "mean": a.get("mean"), "median": a.get("median"),
            "win_rate": a.get("win_rate"), "tail_ratio": a.get("tail_ratio"),
            "pct_up_50": a.get("pct_up_50"), "pct_down_25": a.get("pct_down_25"),
            "mean_vs_whole_pool": d.get("mean_difference"),
            "ci_vs_whole_pool": d.get("mean_ci"),
        }
    out["incremental_value_of_the_upgrade"] = {
        "inside_the_fundamental_cohort": _diff_vs(pool[fq & up], pool[fq & ~up], args),
        "against_covered_but_unchanged": _diff_vs(
            pool[up], pool[_flag(pool, "act_maintain_only_63")], args),
        "against_a_beat_without_an_upgrade": _diff_vs(pool[up & ~beat],
                                                      pool[beat & ~up], args),
        "inside_the_top_momentum_quintile": _diff_vs(pool[strong & up],
                                                     pool[strong & ~up], args),
    }
    out["reading"] = (
        "If upgrade_63_alone is not clearly positive against the pool while the "
        "momentum ranking already is, the overlay's result belongs to M1 and the "
        "analyst data is a passenger. The maintain-only cohort is the control "
        "that separates 'an analyst acted' from 'an analyst covers this name'.")
    return out


def _fmt_ci(ci) -> str:
    if not ci or ci[0] is None:
        return "     n/a"
    return f"[{ci[0]:>6.2f},{ci[1]:>6.2f}]"


def build_answers(ov: dict, attrib: dict, restriction: dict, meta: dict,
                  by: dict) -> dict:
    """The study's ten questions, plus the seven the completion pass asked."""
    coh = attrib["cohorts"]
    inc = attrib["incremental_value_of_the_upgrade"]

    def sub(name: str, key: str = "mean_difference"):
        return ((by.get(name) or {}).get("subset_vs_pool") or {}).get(key)

    def rnd(name: str, split: str, n: int, key: str = "mean_difference"):
        node = ((by.get(name) or {}).get(split) or {}).get("vs_random_top{}".format(n))
        return (node or {}).get(key)

    def cov(name: str, key: str = "share_of_pool_pct"):
        return ((by.get(name) or {}).get("coverage") or {}).get(key)

    def tail(name: str, key: str):
        return ((by.get(name) or {}).get("tail_profile") or {}).get(key)

    def tick_ci(name: str):
        node = (((by.get(name) or {}).get("train") or {}).get("top50") or {})
        return (node.get("selection_vs_pool") or {}).get("ticker_cluster_ci")

    def wf(name: str):
        return [w.get("mean_difference_vs_random")
                for w in ((by.get(name) or {}).get("walk_forward") or [])]

    def cap(name: str, label: str):
        node = (((by.get(name) or {}).get("train") or {}).get("top50") or {})
        return (node.get("capture_pool_" + label) or {}).get("lift")

    def c(name: str, key: str = "mean_vs_whole_pool"):
        return (coh.get(name) or {}).get(key)

    L: list[str] = []
    A = L.append
    A("=" * 78)
    A("7. DIRECT ANSWERS")
    A("=" * 78)
    A("")
    A("A. THE COMPLETION PASS")
    A("")
    A("1. Does full coverage change any verdict?  ONE FLIPS, AND EVERY EFFECT")
    A("   SHRINKS. upgrade_plus_revenue_surprise falls from USEFUL_EVENT_ANNOTATION")
    A("   to INCONCLUSIVE (top-50 vs random in train {} -> {}). The other")
    A("   seventeen keep their labels, but the numbers behind them roughly halve:")
    A("   the tagged-subset edge of the headline overlay goes 1.089 -> {}pp and")
    A("   its walk-forward record goes 2/2 positive to {}/2. Two readings are")
    A("   formally withdrawn — see section 6. The direction of travel is the")
    A("   important part: MORE data made this weaker, not stronger.")
    L[-6] = L[-6].format(PARTIAL_PASS_REFERENCE["overlays"][
        "upgrade_plus_revenue_surprise"]["vs_random_t50_train"],
        rnd("upgrade_plus_revenue_surprise", "train", 50))
    L[-4] = L[-4].format(sub("positive_action_momentum"))
    L[-3] = L[-3].format(sum(1 for x in wf("positive_action_momentum") if (x or -1) > 0))
    A("")
    A("2. Do repeated_upgrades and upgrade_into_strong_momentum clear the 10%")
    A("   coverage floor?  NO, AND THEY NEVER WILL. Their coverage barely moved:")
    A("   repeated_upgrades {}% -> {}%, upgrade_into_strong_momentum {}% -> {}%.".format(
        PARTIAL_PASS_REFERENCE["overlays"]["repeated_upgrades"]["cov"],
        cov("repeated_upgrades"),
        PARTIAL_PASS_REFERENCE["overlays"]["upgrade_into_strong_momentum"]["cov"],
        cov("upgrade_into_strong_momentum")))
    A("   The share of M1 rows carrying two upgrades from two houses in a quarter is")
    A("   a property of how analysts behave, not of how many tickers were fetched —")
    A("   completing the pool doubled the ROW COUNT and left the SHARE where it was.")
    A("   What did improve is list-fill: {:.0f} names on the median date for".format(
        cov("repeated_upgrades", "median_names_per_date") or 0))
    A("   repeated_upgrades against a 25-name bar (it was {}), and {:.0f} for".format(
        PARTIAL_PASS_REFERENCE["overlays"]["repeated_upgrades"]["per_date"],
        cov("upgrade_into_strong_momentum", "median_names_per_date") or 0))
    A("   upgrade_into_strong_momentum. So the sparsity objection is now precisely")
    A("   one objection, not two: these cohorts CAN fill a short list, they just")
    A("   describe too little of the pool for this study to judge them on.")
    A("")
    A("3. Do they survive walk-forward and the 2025 contextual holdout?  REPEATED")
    A("   UPGRADES DOES; THE MOMENTUM INTERACTION SURVIVES THE TESTS AND FAILS THE")
    A("   TAIL CHECK. repeated_upgrades: top-50 beats a random M1 draw by {} in".format(
        rnd("repeated_upgrades", "train", 50)))
    A("   train with CI {}, {} in the spent 2025 holdout, positive in both".format(
        rnd("repeated_upgrades", "train", 50, "mean_ci"),
        rnd("repeated_upgrades", "holdout", 50)))
    A("   walk-forward steps ({}), catastrophic-loser rate at {}x the pool's.".format(
        wf("repeated_upgrades"), cap("repeated_upgrades", "catastrophic")))
    A("   upgrade_into_strong_momentum posts bigger numbers ({} train, {} holdout,".format(
        rnd("upgrade_into_strong_momentum", "train", 50),
        rnd("upgrade_into_strong_momentum", "holdout", 50)))
    A("   walk-forward {}) but now raises the +50% rate {}% AND the -25% rate".format(
        wf("upgrade_into_strong_momentum"),
        tail("upgrade_into_strong_momentum", "right_tail_change_pct")))
    A("   {}% — on the partial pool its left tail FELL {}%. It is selecting".format(
        tail("upgrade_into_strong_momentum", "left_tail_change_pct"), 12.4))
    A("   liveness, which is the exact failure mode the tail test exists to catch.")
    A("   Neither one clears the per-name bar: ticker-clustered CIs {} and {}.".format(
        tick_ci("repeated_upgrades"), tick_ci("upgrade_into_strong_momentum")))
    A("")
    A("4. Does any feature become a PROMISING_CONVICTION_OVERLAY?  NO. Zero, on")
    A("   complete coverage as on partial. The binding failure is the same one the")
    A("   whole programme keeps hitting: every ticker-clustered CI at the primary")
    A("   horizon straddles zero, so the basket moves and the typical name does not.")
    A("")
    A("5. Or does analyst action remain only a USEFUL_EVENT_ANNOTATION?  YES —")
    A("   three of them: positive_action_momentum, large_rating_improvement and")
    A("   new_bullish_coverage. The honest sentence for a tagged name is unchanged")
    A("   but weaker than the partial run suggested: 'analysts have been raising")
    A("   their ratings here, and names like it lost 25%+ about {}% less often'.".format(
        abs(tail("positive_action_momentum", "left_tail_change_pct") or 0)))
    A("   It is a risk annotation. It must not order a list.")
    A("")
    A("6. Should price-target-news still be tested next?  YES, WITH A LOWER PRIOR.")
    A("   The reason is unchanged and now measured on the whole pool: {}% of the".format(
        meta["action_class_mix_pct"].get("maintain")))
    A("   feed is MAINTAINS carrying no rating change, they are invisible to every")
    A("   feature built here, and that cohort is flat ({}pp {}). A dated".format(
        c("maintain_only_63"), c("maintain_only_63", "ci_vs_whole_pool")))
    A("   price target with priceWhenPosted is the numeric revision this feed")
    A("   cannot express, and it is the closest available substitute for the")
    A("   estimate-revision breadth FMP does not serve. The lower prior is earned:")
    A("   completing THIS dataset halved its effects, so the honest expectation for")
    A("   the next one is a sub-1pp annotation, and the bars should be")
    A("   pre-registered before the fetch, exactly as they were here.")
    A("")
    A("7. Should this study be committed?  YES. It is a complete, negative-to-")
    A("   neutral result with reproducible artifacts, containment tests, and a")
    A("   documented correction of its own earlier partial run — that correction is")
    A("   the most valuable thing in it. Commit the module, the tests, the write")
    A("   prefixes and the API-key redaction fix. The report and JSON artifacts live")
    A("   under cache/ and logs/, which are gitignored, so the finding needs a")
    A("   docs/research/ memo if it is to survive in the record.")
    A("")
    A("B. THE STUDY'S OWN TEN QUESTIONS")
    A("")
    A("1. Does analyst-action momentum explain any missing M1 dispersion?  A LITTLE,")
    A("   AND NOT DIRECTIONALLY. Upgrade in the trailing quarter: {}pp against".format(
        c("upgrade_63_alone")))
    A("   the pool at 60d {}. Downgrade: {}pp {} — a different".format(
        c("upgrade_63_alone", "ci_vs_whole_pool"), c("downgrade_63_alone"),
        c("downgrade_63_alone", "ci_vs_whole_pool")))
    A("   number from the upgrade but the same sign, on overlapping intervals. What")
    A("   separates cohorts is whether a rating CHANGED at all: maintain-only sits")
    A("   at {}pp and no action at all at {}pp, which on complete coverage".format(
        c("maintain_only_63"), c("no_action_126")))
    A("   is flat {} — the partial run's 'uncovered names underperform' was".format(
        c("no_action_126", "ci_vs_whole_pool")))
    A("   an artifact of the missing tickers and is withdrawn.")
    A("   What moves is the left tail: the upgraded subset's -25% rate falls {}%".format(
        abs(tail("positive_action_momentum", "left_tail_change_pct") or 0)))
    A("   against a {}% change in its +50% rate, with a catastrophic-loser lift".format(
        tail("positive_action_momentum", "right_tail_change_pct")))
    A("   of {}x at top-50. Against the ~69-98pp of oracle headroom the".format(
        cap("positive_action_momentum", "catastrophic")))
    A("   missing-data audit measured, this is a rounding error.")
    A("")
    A("2. Do upgrades help more than earnings surprise?  YES. Upgrade alone {}pp".format(
        c("upgrade_63_alone")))
    A("   {} vs EPS beat alone {}pp {}. An upgrade WITHOUT a beat".format(
        c("upgrade_63_alone", "ci_vs_whole_pool"), c("eps_beat_alone"),
        c("eps_beat_alone", "ci_vs_whole_pool")))
    A("   beats a beat WITHOUT an upgrade by {}pp CI {}, and stacking".format(
        (inc.get("against_a_beat_without_an_upgrade") or {}).get("mean_difference"),
        (inc.get("against_a_beat_without_an_upgrade") or {}).get("mean_ci")))
    A("   the two is worth less than either alone (upgrade_and_beat {}pp).".format(
        c("upgrade_and_beat")))
    A("")
    A("3. Do downgrades work as an avoid filter?  NO — the reverse. Downgraded names")
    A("   BEAT the pool by {}pp {}. Dropping them therefore leaves a".format(
        c("downgrade_63_alone"), c("downgrade_63_alone", "ci_vs_whole_pool")))
    A("   pool {}pp worse at 60d than the names it dropped (CI {}),".format(
        sub("avoid_downgrade"), sub("avoid_downgrade", "mean_ci")))
    A("   while raising both tails ({}% right, {}% left). Restricting it to".format(
        tail("avoid_downgrade", "right_tail_change_pct"),
        tail("avoid_downgrade", "left_tail_change_pct")))
    A("   downgrades that land while the name is in the pool's top momentum quintile")
    A("   does not rescue it ({}pp).".format(sub("avoid_downgrade_despite_strength")))
    A("")
    A("4. Does repeated upgrade activity matter?  IT IS THE BEST ANALYST-ONLY COHORT")
    A("   AND IT IS STILL REFUSED. {}pp vs the pool {}, walk-forward".format(
        sub("repeated_upgrades"), sub("repeated_upgrades", "mean_ci")))
    A("   {}, top-50 vs random {} {} in train — the only overlay whose".format(
        wf("repeated_upgrades"), rnd("repeated_upgrades", "train", 50),
        rnd("repeated_upgrades", "train", 50, "mean_ci")))
    A("   train CI clears zero at top-50. It covers {}% of rows, so the".format(
        cov("repeated_upgrades")))
    A("   pre-registered floor refuses it. See completion-pass answer 2.")
    A("")
    A("5. Does analyst action improve top-25/top-50 ranking?  NO, ON COMPLETE")
    A("   COVERAGE. The partial run's marginal filter result did not survive:")
    A("   positive_action_momentum at top-50 is now {} {} in train,".format(
        rnd("positive_action_momentum", "train", 50),
        rnd("positive_action_momentum", "train", 50, "mean_ci")))
    A("   a CI straddling zero where it previously cleared it, and it is negative in")
    A("   the 2024 walk-forward step. Re-ordering the whole pool by net upgrades is")
    A("   worth {} in train and is negative in BOTH walk-forward steps {}.".format(
        rnd("rank_by_net_upgrades", "train", 50), wf("rank_by_net_upgrades")))
    A("   Only repeated_upgrades ranks, and only on 4.2% of the pool.")
    A("")
    A("6. Does it improve per-name confidence?  NO. Every ticker-clustered CI at the")
    A("   primary horizon straddles zero: positive_action_momentum {},".format(
        tick_ci("positive_action_momentum")))
    A("   repeated_upgrades {}, upgrade_into_strong_momentum {}.".format(
        tick_ci("repeated_upgrades"), tick_ci("upgrade_into_strong_momentum")))
    A("   The partial run's single positive (45d, one horizon of five) did not")
    A("   survive completion — which is what a multiple-comparisons artifact does.")
    A("")
    A("7. Conviction overlay or annotation?  ANNOTATION, three of them.")
    A("")
    A("8. Should price-target-news be tested next?  Yes — see completion-pass 6.")
    A("")
    A("9. Should grades-historical be tested next?  NO. Monthly aggregated counts of")
    A("   the actions already fetched here, coarser, with restatement risk, and now")
    A("   demonstrably measuring something whose effects halve under better data.")
    A("")
    A("10. Or should we stop and accept M1 as a wide source pool?  YES — and the")
    A("    measurement debt this answer carried last time is now PAID. Coverage is")
    A("    3,640 of 3,640 tickers, the cohorts that looked promising were re-measured")
    A("    on the complete pool, and they shrank. M1 stays a wide, equal-weight,")
    A("    unordered source pool. Analyst actions annotate it; they do not order it.")
    A("")
    A("=" * 78)
    A("BOTTOM LINE")
    A("=" * 78)
    A("The completed pass is the result. A rating CHANGE — in either direction —")
    A("marks a name that does slightly better than one nobody re-rated, the upgrade")
    A("tag trims the left tail by about a fifth without adding to the right, and")
    A("nothing here orders a list. Every ticker-clustered interval straddles zero.")
    A("")
    A("The most useful output of this pass is not a finding but a correction. The")
    A("56%-coverage run overstated every effect it measured, by roughly a factor of")
    A("two, and produced two readings that are now withdrawn. The gap was caused by")
    A("provider rate-limit rejections — a plumbing failure that looked exactly like")
    A("data while it was in the artifacts. The lesson is procedural and worth more")
    A("than the analyst signal: a partial provider pass must be labelled and")
    A("re-measured, never read.")
    A("")
    headline = (
        "On complete coverage (3,640/3,640 tickers) analyst actions remain an "
        "annotation inside M1, never a ranker: an upgrade is worth {}pp vs the pool "
        "at 60d {}, a downgrade {}pp, no overlay earned a conviction verdict, and "
        "every ticker-clustered CI straddles zero. Completing the coverage HALVED "
        "every effect the 56% run reported and withdrew two of its readings."
    ).format(c("upgrade_63_alone"), c("upgrade_63_alone", "ci_vs_whole_pool"),
             c("downgrade_63_alone"))
    answers = {
        "1_explains_missing_dispersion": (
            "A little, non-directionally: upgrade {}pp {}, downgrade {}pp; what "
            "separates cohorts is whether a rating changed (maintain-only {}, no "
            "action {}).").format(
                c("upgrade_63_alone"), c("upgrade_63_alone", "ci_vs_whole_pool"),
                c("downgrade_63_alone"), c("maintain_only_63"), c("no_action_126")),
        "2_upgrades_vs_earnings_surprise": (
            "Upgrades win: {}pp vs beat {}pp; stacking them is worth less than "
            "either alone.").format(c("upgrade_63_alone"), c("eps_beat_alone")),
        "3_downgrades_as_avoid_filter": (
            "No, the reverse: downgraded names beat the pool by {}pp, so the filter "
            "leaves the pool {}pp worse and raises both tails.").format(
                c("downgrade_63_alone"), sub("avoid_downgrade")),
        "4_repeated_upgrades": (
            "Best analyst-only cohort ({}pp vs pool, walk-forward {}, the only "
            "top-50 train CI clear of zero) but {}% coverage — refused by the "
            "pre-registered floor.").format(
                sub("repeated_upgrades"), wf("repeated_upgrades"),
                cov("repeated_upgrades")),
        "5_improves_top25_top50": (
            "No on complete coverage: {} {} at top-50 in train, negative in the "
            "2024 step; the whole-pool ranker is {} and negative in both steps."
        ).format(rnd("positive_action_momentum", "train", 50),
                 rnd("positive_action_momentum", "train", 50, "mean_ci"),
                 rnd("rank_by_net_upgrades", "train", 50)),
        "6_improves_per_name_confidence": (
            "No — every ticker-clustered CI straddles zero; the partial run's single "
            "positive did not survive completion."),
        "7_overlay_or_annotation": "Annotation. Three USEFUL_EVENT_ANNOTATION, zero "
                                   "conviction verdicts.",
        "8_test_price_target_news_next": (
            "Yes, with a lower prior: {}% of actions are maintains carrying no "
            "rating change and that cohort is flat ({}pp)."
        ).format(meta["action_class_mix_pct"].get("maintain"), c("maintain_only_63")),
        "9_test_grades_historical_next": "No — coarser aggregation of the same events.",
        "10_stop_and_accept_m1_as_a_wide_pool": (
            "Yes; the coverage debt is paid (3,640/3,640) and the promising cohorts "
            "shrank on re-measurement."),
    }
    completion = {
        "1_full_coverage_changes_verdicts": (
            "One flips (upgrade_plus_revenue_surprise annotation -> inconclusive) and "
            "every effect roughly halves; two partial-run readings withdrawn."),
        "2_cohorts_clear_the_10pct_floor": (
            "No: repeated_upgrades {}%, upgrade_into_strong_momentum {}% — coverage "
            "is a property of analyst behaviour, not of the fetch. List-fill did "
            "improve ({:.0f} and {:.0f} names per date)."
        ).format(cov("repeated_upgrades"), cov("upgrade_into_strong_momentum"),
                 cov("repeated_upgrades", "median_names_per_date") or 0,
                 cov("upgrade_into_strong_momentum", "median_names_per_date") or 0),
        "3_walk_forward_and_2025_context": (
            "repeated_upgrades survives both (train {} {}, holdout {}, walk-forward "
            "{}); upgrade_into_strong_momentum survives the tests but now amplifies "
            "BOTH tails ({}% / {}%). Neither clears the per-name bar."
        ).format(rnd("repeated_upgrades", "train", 50),
                 rnd("repeated_upgrades", "train", 50, "mean_ci"),
                 rnd("repeated_upgrades", "holdout", 50), wf("repeated_upgrades"),
                 tail("upgrade_into_strong_momentum", "right_tail_change_pct"),
                 tail("upgrade_into_strong_momentum", "left_tail_change_pct")),
        "4_any_promising_conviction_overlay": "No — zero, on complete coverage.",
        "5_remains_useful_event_annotation": (
            "Yes: positive_action_momentum, large_rating_improvement, "
            "new_bullish_coverage."),
        "6_price_target_news_next": (
            "Yes, with a lower prior and pre-registered bars — it measures the 86% of "
            "the feed that carries no rating change."),
        "7_commit_this_study": (
            "Yes — module, tests, write prefixes and the redaction fix; the artifacts "
            "are gitignored, so the finding needs a docs/research memo to survive."),
    }
    return {"text": "\n".join(L), "headline": headline, "answers": answers,
            "completion_pass_answers": completion}


def report_stage(args) -> int:
    root = Path(args.root)
    tw = LiveArtifactTripwire.snapshot(root)
    ov = json.loads((root / OVERLAY_REL).read_text())
    meta = json.loads((root / PANEL_META_REL).read_text())
    fetch = json.loads((root / FETCH_META_REL).read_text())
    pool_all, base = load_action_panel(root)
    pool, restriction = _restrict_to_fetched(pool_all, base, args)
    attrib = attribution_test(pool, args)
    by = {o["name"]: o for o in ov["overlays"]}
    coh = attrib["cohorts"]

    def cell(name: str, *path, default=None):
        node = by.get(name) or {}
        for k in path:
            node = (node or {}).get(k) or {}
        return node if node else (default if default is not None else {})

    L: list[str] = []
    A = L.append
    A("=" * 78)
    A("M1 ANALYST-ACTION MOMENTUM — do dated upgrades rank inside the momentum pool?")
    A("=" * 78)
    A(f"Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')} · "
      f"{fetch['calls_made']:,} provider calls of a {fetch['hard_cap']:,} cap, "
      f"endpoint {fetch['endpoint']} only")
    A("")
    A("RESEARCH ONLY. Replay evidence over one fixed historical window. Not live")
    A("forward evidence, not a gate/threshold/score change, not a trade signal, and")
    A("not a recommendation to route anything anywhere.")
    A("")
    A("-" * 78)
    A("1. THE DATA, AND THE HOLE IN IT")
    A("-" * 78)
    A(f"One pass over the frozen M1 pool: {fetch['planned_calls']:,} tickers, one "
      f"{fetch['endpoint']} call each.")
    A(f"{fetch.get('tickers_ok', restriction['tickers_kept']):,} returned; "
      f"{restriction['tickers_dropped']:,} were rejected by the provider's rate "
      f"limiter (HTTP 429).")
    A(f"Retrying them would cost ~{restriction['tickers_dropped']:,} more calls, "
      f"which is outside the approved cap, so THIS RUN IS PARTIAL COVERAGE and the")
    A("gap is carried explicitly rather than papered over:")
    A("")
    A(f"  pool rows held out         : {restriction['rows_dropped']:,} of "
      f"{ov['pool_rows_before_fetch_gap']:,} ({restriction['rows_dropped_pct']}%)")
    A(f"  names per date, before/after: "
      f"{restriction['median_names_per_date_before']:.0f} -> "
      f"{restriction['median_names_per_date_after']:.0f}")
    fu = restriction.get(f"full_pool_vs_universe_{PRIMARY_H}d") or {}
    ru = restriction.get(f"restricted_pool_vs_universe_{PRIMARY_H}d") or {}
    A(f"  M1 vs same-date universe   : full pool {fu.get('mean_difference')}pp "
      f"{fu.get('date_cluster_ci')} · restricted pool {ru.get('mean_difference')}pp "
      f"{ru.get('date_cluster_ci')}")
    A("")
    A("  The restriction did not change what M1 is: the kept pool's edge over the")
    A("  same-date universe is within noise of the full pool's. A dropped row is a")
    A("  call that never returned, NOT a name without analyst coverage — those rows")
    A("  are nulled and held out of every overlay and every control.")
    A("")
    for k, v in meta["coverage"].items():
        A(f"  {k}: {v}")
    A("")
    A(f"  Action mix in the feed: {meta['action_class_mix_pct']} "
      f"(derived from the rating PAIR, not the provider's label)")
    A(f"  Provider label agreement: {meta['provider_action_label_agreement_pct']}% · "
      f"unmapped grade strings: {meta['unmapped_new_grade_pct']}%")
    A(f"  Point-in-time rule: {meta['point_in_time_rule']}")
    A(f"  Lookahead audit: {meta['lookahead_audit']['rows_checked']} rows recounted "
      f"directly from the raw feed; none used an action that was not yet usable.")
    A("")
    A("  FEED PROVENANCE — the risk that cannot be closed from a current pull:")
    A(f"  {meta['feed_provenance_warning']}")
    A("")

    A("-" * 78)
    A("2. THE OVERLAYS")
    A("-" * 78)
    A(f"{'':2}{'overlay':<34}{'cov%':>6}{'/date':>6}{'sub vs pool':>13}"
      f"{'t50 vs rand tr':>16}{'ho':>8}{'right':>8}{'left':>8}  verdict")
    for o in ov["overlays"]:
        sv = o.get("subset_vs_pool") or {}
        vr = cell(o["name"], "train", "vs_random_top50")
        hr = cell(o["name"], "holdout", "vs_random_top50")
        tp = o.get("tail_profile") or {}
        A(f"  {o['name']:<34}{o['coverage']['share_of_pool_pct']:>6}"
          f"{o['coverage']['median_names_per_date']:>6.0f}"
          f"{str(sv.get('mean_difference')):>13}{str(vr.get('mean_difference')):>16}"
          f"{str(hr.get('mean_difference')):>8}"
          f"{str(tp.get('right_tail_change_pct')):>8}"
          f"{str(tp.get('left_tail_change_pct')):>8}  {o.get('verdict')}")
    A("")
    A(f"Verdict counts: {json.dumps(ov['verdict_counts'])}")
    A("")
    A("'sub vs pool' is the tagged subset against the rest of the pool at 60d.")
    A("'t50 vs rand' is a top-50 built by the overlay against a RANDOM 50 drawn")
    A("from the same M1 pool on the same dates. Right/left are the % changes in the")
    A("+50% and -25% rates of the tagged subset against the rest of the pool.")
    A("")

    A("-" * 78)
    A("3. THE COVERAGE GRADIENT — what is actually carrying the information")
    A("-" * 78)
    A(f"{'':2}{'cohort':<36}{'rows':>9}{'mean':>8}{'median':>8}{'win%':>7}"
      f"{'+50%':>7}{'-25%':>7}{'vs pool':>9}  CI")
    for k in ("whole_pool", "upgrade_63_alone", "maintain_only_63", "no_action_126",
              "downgrade_63_alone", "eps_beat_alone", "upgrade_and_beat",
              "upgrade_without_beat", "beat_without_upgrade",
              "fundamental_quality_alone", "fundamental_quality_plus_upgrade",
              "top_momentum_quintile_alone", "top_momentum_without_upgrade",
              "top_momentum_and_upgrade", "upgrade_outside_top_momentum"):
        v = coh.get(k) or {}
        if not v.get("rows"):
            continue
        A(f"  {k:<36}{v['rows']:>9,}{str(v['mean']):>8}{str(v['median']):>8}"
          f"{str(v['win_rate']):>7}{str(v['pct_up_50']):>7}{str(v['pct_down_25']):>7}"
          f"{str(v['mean_vs_whole_pool']):>9}  {v.get('ci_vs_whole_pool')}")
    A("")
    inc = attrib["incremental_value_of_the_upgrade"]
    for k, v in inc.items():
        v = v or {}
        A(f"  incremental value of the upgrade {k:<34}: "
          f"{v.get('mean_difference')}pp CI {v.get('mean_ci')} "
          f"({v.get('dates_won_pct')}% of dates)")
    A("")

    A("-" * 78)
    A("4. HORIZON PROFILE OF THE THREE STRONGEST TAGS")
    A("-" * 78)
    A(f"{'':2}{'tag':<30}{'h':>6}{'mean':>8}{'median':>8}{'win%':>7}{'tailR':>7}"
      f"{'vs pool':>9}{'vs univ':>9}  ticker-clustered CI")
    for name in ("positive_action_momentum", "repeated_upgrades",
                 "upgrade_into_strong_momentum"):
        for h, v in (by.get(name, {}).get("subset_by_horizon") or {}).items():
            A(f"  {name:<30}{h:>6}{str(v['mean']):>8}{str(v['median']):>8}"
              f"{str(v['win_rate']):>7}{str(v['tail_ratio']):>7}"
              f"{str(v['selection_vs_pool_mean']):>9}"
              f"{str((v.get('selection_vs_universe') or {}).get('mean_difference')):>9}"
              f"  {v['ticker_cluster_ci']}")
    A("")

    A("-" * 78)
    A("5. WALK-FORWARD AND HOLDOUT")
    A("-" * 78)
    A(f"{'':2}{'overlay':<34}{'2022->2023':>12}{'2023->2024':>12}"
      f"{'2025 (spent)':>14}")
    for o in ov["overlays"]:
        wf = {w["tested_on"]: w["mean_difference_vs_random"]
              for w in (o.get("walk_forward") or [])}
        ho = cell(o["name"], "holdout", "vs_random_top50").get("mean_difference")
        A(f"  {o['name']:<34}{str(wf.get(2023)):>12}{str(wf.get(2024)):>12}"
          f"{str(ho):>14}")
    A("")
    A("2025 is reported as ALREADY-SPENT context, not clean discovery: earlier")
    A("studies in this programme have already looked at it.")
    A("")

    A("-" * 78)
    A("6. WHAT COMPLETING THE COVERAGE CHANGED")
    A("-" * 78)
    A("Reference run: " + PARTIAL_PASS_REFERENCE["recorded"] + ".")
    A(PARTIAL_PASS_REFERENCE["status"] + ".")
    A("")
    A("  {:<32}{:>13}{:>13}{:>21}{:>18}".format(
        "overlay", "cov% p1/p2", "/date p1/p2", "t50 vs rand train", "holdout"))
    for name, ref in PARTIAL_PASS_REFERENCE["overlays"].items():
        c = (by.get(name) or {}).get("coverage") or {}
        tr = cell(name, "train", "vs_random_top50").get("mean_difference")
        ho = cell(name, "holdout", "vs_random_top50").get("mean_difference")
        A("  {:<32}{:>13}{:>13}{:>21}{:>18}".format(
            name,
            "{}/{}".format(ref["cov"], c.get("share_of_pool_pct")),
            "{}/{:.0f}".format(ref["per_date"], c.get("median_names_per_date") or 0),
            "{} -> {}".format(ref["vs_random_t50_train"], tr),
            "{} -> {}".format(ref["vs_random_t50_holdout"], ho)))
    A("")
    A("  {:<34}{:>10}{:>10}".format("cohort vs pool at 60d", "partial", "complete"))
    for k, v in PARTIAL_PASS_REFERENCE["cohorts_vs_pool"].items():
        A("  {:<34}{:>10}{:>10}".format(
            k, v, str((coh.get(k) or {}).get("mean_vs_whole_pool"))))
    A("")
    A("  Verdicts: " + json.dumps(PARTIAL_PASS_REFERENCE["verdict_counts"]))
    A("         -> " + json.dumps(ov["verdict_counts"]))
    A("")
    A("  Completing the coverage roughly HALVED every effect in the study. The")
    A("  partial pool was not a random half — it was whatever the provider's rate")
    A("  limiter happened to let through — and it flattered every cohort. Two")
    A("  readings from that run are formally WITHDRAWN: 'names with no analyst")
    A("  coverage underperform' (-0.74pp then, flat now) and 'upgrades into strong")
    A("  momentum improve the asymmetry' (that cohort now raises BOTH tails).")
    A("")

    text_head = "\n".join(L)
    answers = build_answers(ov, attrib, restriction, meta, by)
    text = text_head + "\n" + answers["text"]
    write_replay_text(root / REPORT_TXT_REL, text + "\n", root=root)

    summary = {
        "kind": "M1_ANALYST_ACTIONS_LATEST",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "endpoint": fetch["endpoint"],
        "provider_calls_made": fetch["calls_made"],
        "hard_cap": fetch["hard_cap"],
        "coverage_gap": {
            "tickers_without_a_response": restriction["tickers_dropped"],
            "pool_rows_held_out_pct": restriction["rows_dropped_pct"],
            "retry_cost_calls": restriction["tickers_dropped"],
            "status": "PARTIAL_COVERAGE — a completing pass needs operator approval "
                      "for calls beyond the 3,800 already spent",
        },
        "verdict_counts": ov["verdict_counts"],
        "overlay_verdicts": {o["name"]: o.get("verdict") for o in ov["overlays"]},
        "headline": answers["headline"],
        "answers": answers["answers"],
        "completion_pass_answers": answers["completion_pass_answers"],
        "coverage_status": "COMPLETE — 3,640 of 3,640 M1 tickers returned",
        "partial_pass_reference": PARTIAL_PASS_REFERENCE,
        "attribution": attrib,
        "not_supported": [
            "using analyst actions to ORDER an M1 list",
            "using a downgrade as an avoid filter",
            "any production, gate, score, routing or dashboard change",
            "reading the sparse high-scoring cohorts as validated",
        ],
        "report_txt": REPORT_TXT_REL,
        "artifacts": [OVERLAY_REL, PANEL_META_REL, OUTCOMES_REL],
        **provenance_flags(),
    }
    assert_provenance(summary)
    write_replay_json(root / LATEST_REL, summary, root=root)
    print(tw.report())
    tw.assert_clean()
    print(text)
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="m1_analyst_actions")
    sub = ap.add_subparsers(dest="stage", required=True)
    for name in ("plan", "fetch", "panel", "overlay", "report", "all"):
        s = sub.add_parser(name)
        s.add_argument("--root", default=str(ROOT))
        s.add_argument("--start", default=REPLAY_WINDOW_START)
        s.add_argument("--end", default=REPLAY_WINDOW_END)
        s.add_argument("--execute-fetch", action="store_true")
        s.add_argument("--max-calls", type=int, default=None)
        s.add_argument("--limit", type=int, default=0)
        s.add_argument("--workers", type=int, default=3)
        s.add_argument("--retries", type=int, default=2)
        s.add_argument("--backoff", type=float, default=2.0)
        s.add_argument("--rebuild-tickers", action="store_true")
        s.add_argument("--only-failed", action="store_true",
                       help="completion pass: re-fetch only the tickers whose "
                            "call never returned, appending to the raw feed")
        s.add_argument("--bootstrap", type=int, default=1500)
        s.add_argument("--seed", type=int, default=31)
    return ap


STAGE_ORDER: tuple[str, ...] = ("panel", "overlay", "report")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.stage != "fetch":
        # Every stage but the fetch is offline: force the cred-free import path
        # so an analysis run cannot reach a provider even by accident.
        offline_env()
    if args.stage == "all":
        for name in STAGE_ORDER:
            print(f"\n{'=' * 70}\n== {name}\n{'=' * 70}", flush=True)
            rc = run_cli(globals()[f"{name}_stage"], args)
            if rc:
                return rc
        return 0
    fn = globals().get(f"{args.stage}_stage")
    if fn is None:
        print(f"stage {args.stage!r} not implemented yet")
        return 2
    return run_cli(fn, args)


if __name__ == "__main__":
    raise SystemExit(main())
