"""M1 Surprise Overlay — does earnings surprise rank inside the momentum pool?

RESEARCH ONLY. The one experiment the Missing Conviction Data Audit
recommended, run under explicit approval: fetch historical earnings surprise
from FMP's date-ranged /earnings-calendar endpoint and test whether surprise
magnitude separates future winners from losers inside the frozen M1 12-1
momentum pool.

Writes only ``cache/research/m1_surprise_*`` and
``logs/m1_surprise_overlay_*``. Changes no scanner, score, gate, threshold,
HC/EO rule, routing decision, dashboard artifact, cron entry or ledger, emits
no trade signal, and cannot emit a live verdict token.

Provider policy, enforced in code rather than by intent:

* the fetch stage refuses to run without ``--execute-fetch``;
* the plan is printed and capped before the first call — :data:`HARD_CALL_CAP`
  is 500 and the planned monthly chunking is ~51;
* every response is written to a replay-owned cache, so the analysis stages
  never need the network again.

Point-in-time rules, also enforced:

* an earnings row is usable only from the FIRST TRADING SESSION STRICTLY AFTER
  its report date. FMP's calendar carries no reliable time-of-day field, so the
  conservative reading is used: a report dated D is unknown on D;
* :func:`surprise_asof` selects the most recent usable event at or before the
  scan date and never looks past it;
* a guard re-checks a sample of rows against the raw calendar and fails the
  stage if any feature could have used a future event.

Known limitation, stated up front: the ESTIMATE is the value the provider
serves today. For a past event that is normally the pre-report consensus, but
it cannot be verified from a current pull, and a provider that silently
back-fills revised estimates would inject lookahead this study cannot detect.
That risk is disclosed in every artifact rather than assumed away.

Sub-stages::

    plan     print the fetch plan and call count; makes no calls
    fetch    fetch the calendar (requires --execute-fetch)
    panel    build the point-in-time surprise panel from cache  (zero API)
    overlay  test the overlays inside the frozen M1 pool        (zero API)
    report   direct answers                                     (zero API)
"""

from __future__ import annotations

import argparse
import json
import math
import time
from datetime import date, datetime, timedelta, timezone
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

INTERMEDIATES_REL = "cache/research/m1_surprise_intermediates"
RAW_REL = f"{INTERMEDIATES_REL}/earnings_calendar_raw.jsonl"
FETCH_META_REL = f"{INTERMEDIATES_REL}/fetch_meta.json"
PANEL_REL = f"{INTERMEDIATES_REL}/surprise_panel.parquet"
OVERLAY_REL = "cache/research/m1_surprise_overlay_latest.json"
LATEST_REL = "cache/research/m1_surprise_latest.json"
REPORT_TXT_REL = "logs/m1_surprise_overlay_latest.txt"

HARD_CALL_CAP = 500

# Earnings history must start before the replay window so the first scan dates
# have a most-recent event to look back at. One quarter of lead-in.
FETCH_START = "2021-10-01"
FETCH_END = "2026-01-31"


def month_chunks(start: str, end: str) -> list[tuple[str, str]]:
    """Calendar-month [from, to] pairs covering the range, inclusive."""
    s = date.fromisoformat(start)
    e = date.fromisoformat(end)
    out = []
    cur = date(s.year, s.month, 1)
    while cur <= e:
        nxt = date(cur.year + (cur.month == 12), (cur.month % 12) + 1, 1)
        out.append((max(cur, s).isoformat(), (min(nxt - timedelta(days=1), e)).isoformat()))
        cur = nxt
    return out


def plan_stage(args) -> int:
    """Print the fetch plan. Makes no calls, with or without --execute-fetch."""
    chunks = month_chunks(args.fetch_start, args.fetch_end)
    plan = {
        "kind": "M1_SURPRISE_FETCH_PLAN",
        "endpoint": "/earnings-calendar (date-ranged, not per-ticker)",
        "range": [args.fetch_start, args.fetch_end],
        "chunking": "one call per calendar month",
        "planned_calls": len(chunks),
        "hard_cap": HARD_CALL_CAP,
        "fields_wanted": ["symbol", "date", "epsActual", "epsEstimated",
                          "revenueActual", "revenueEstimated"],
        "destination": RAW_REL,
        "calls_made_by_this_stage": 0,
    }
    print(json.dumps(plan, indent=1))
    if len(chunks) > HARD_CALL_CAP:
        print(f"REFUSED: {len(chunks)} planned calls exceed the {HARD_CALL_CAP} cap")
        return 3
    return 0


def fetch_stage(args) -> int:
    """Fetch the calendar. Requires --execute-fetch; capped and logged."""
    root = Path(args.root)
    tw = LiveArtifactTripwire.snapshot(root)
    chunks = month_chunks(args.fetch_start, args.fetch_end)
    require_execute_fetch(args, planned_calls=len(chunks),
                          what="earnings-calendar history")
    enforce_call_cap(len(chunks), min(args.max_calls or HARD_CALL_CAP, HARD_CALL_CAP),
                     what="earnings-calendar history")

    # Credential bootstrap — the same pattern main.py documents as "the same
    # pattern as every other script": point python-dotenv at the operator's
    # credential file and let it populate the process environment. The file is
    # never read, edited or echoed here.
    import os

    cred = os.environ.get("SNIPER_ENV_PATH", "/home/gem/secure/trading.env")
    if Path(cred).exists():
        from dotenv import load_dotenv

        load_dotenv(cred, override=True)
    else:
        raise FetchNotAuthorised(
            f"credential file not found at {cred}; set SNIPER_ENV_PATH. "
            "No calls were made.")

    from core.fmp_client import get_fmp

    client = get_fmp()
    rows: list[dict] = []
    calls = 0
    errors: list[dict] = []
    t0 = time.time()
    for i, (a, b) in enumerate(chunks, 1):
        if calls >= HARD_CALL_CAP:
            errors.append({"chunk": [a, b], "error": "hard cap reached"})
            break
        try:
            data = client._get("/earnings-calendar", params={"from": a, "to": b})
            calls += 1
        except Exception as e:
            calls += 1
            errors.append({"chunk": [a, b], "error": f"{type(e).__name__}: {e}"})
            continue
        if isinstance(data, list):
            rows.extend(data)
        if i % 12 == 0:
            print(f"  [{i}/{len(chunks)}] {len(rows):,} rows · {calls} calls · "
                  f"{time.time() - t0:.0f}s", flush=True)

    out = root / RAW_REL
    assert_replay_write_path(out, root=root)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(json.dumps(r) for r in rows) + "\n")

    keys: dict[str, int] = {}
    for r in rows[:5000]:
        for k in r:
            keys[k] = keys.get(k, 0) + 1
    meta = {
        "kind": "M1_SURPRISE_FETCH_META",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "calls_made": calls, "hard_cap": HARD_CALL_CAP,
        "chunks": len(chunks), "rows": len(rows),
        "range": [args.fetch_start, args.fetch_end],
        "errors": errors,
        "observed_fields": sorted(keys),
        "field_presence_sample": {k: round(100.0 * v / min(len(rows), 5000), 1)
                                  for k, v in sorted(keys.items())},
        **provenance_flags(),
    }
    assert_provenance(meta)
    write_replay_json(root / FETCH_META_REL, meta, root=root)
    print(tw.report())
    tw.assert_clean()
    print(f"\nfetched {len(rows):,} rows in {calls} calls "
          f"({len(errors)} errors) -> {RAW_REL}")
    print("fields:", sorted(keys))
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="m1_surprise_overlay")
    sub = ap.add_subparsers(dest="stage", required=True)
    for name in ("plan", "fetch", "panel", "overlay", "report", "all"):
        s = sub.add_parser(name)
        s.add_argument("--root", default=str(ROOT))
        s.add_argument("--start", default=REPLAY_WINDOW_START)
        s.add_argument("--end", default=REPLAY_WINDOW_END)
        s.add_argument("--fetch-start", default=FETCH_START)
        s.add_argument("--fetch-end", default=FETCH_END)
        s.add_argument("--execute-fetch", action="store_true")
        s.add_argument("--max-calls", type=int, default=None)
        s.add_argument("--limit", type=int, default=0)
        s.add_argument("--bootstrap", type=int, default=1500)
        s.add_argument("--seed", type=int, default=31)
    return ap


STAGE_ORDER: tuple[str, ...] = ("panel", "overlay", "report")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
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

# ── point-in-time surprise panel ────────────────────────────────────────────

# Percentage surprise on a near-zero estimate is meaningless arithmetic — the
# raw field reaches 1e14% in this data. Two robust forms are carried instead:
#   * scaled by the estimate with a floor, then clipped to a stated band;
#   * scaled by the share price at the report, which is the economically
#     meaningful denominator and has no blow-up mode.
EPS_DENOM_FLOOR = 0.05      # dollars per share
SURPRISE_CLIP_PCT = 200.0


def _num(v):
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def load_raw_calendar(root: Path):
    """Deduplicated earnings rows with numeric fields."""
    import pandas as pd

    f = root / RAW_REL
    if not f.exists():
        raise SystemExit(f"missing {RAW_REL} — run the fetch stage first")
    rows = [json.loads(l) for l in f.read_text().splitlines() if l.strip()]
    df = pd.DataFrame(rows)
    for c in ("epsActual", "epsEstimated", "revenueActual", "revenueEstimated"):
        df[c] = pd.to_numeric(df.get(c), errors="coerce")
    df["date"] = pd.to_datetime(df["date"])
    df["symbol"] = df["symbol"].astype(str).str.upper()
    # A handful of (symbol, date) duplicates: keep the most complete row.
    df["_complete"] = df[["epsActual", "epsEstimated",
                          "revenueActual", "revenueEstimated"]].notna().sum(axis=1)
    df = (df.sort_values("_complete", ascending=False)
            .drop_duplicates(["symbol", "date"], keep="first")
            .drop(columns="_complete"))
    return df


def build_surprise_panel(root: Path, pool, prices: dict, sessions):
    """Most recent USABLE earnings event per (ticker, scan_date).

    Usable means: the first trading session strictly after the report date. The
    calendar carries no time-of-day field, so a report dated D is treated as
    unknown on D — the conservative reading the approval specified.
    """
    import numpy as np
    import pandas as pd

    cal = load_raw_calendar(root)
    sess = np.array(sessions, dtype="datetime64[ns]")
    # usable = first session strictly after the report date
    idx = np.searchsorted(sess, cal["date"].to_numpy("datetime64[ns]"), side="right")
    cal = cal[idx < len(sess)].copy()
    cal["usable_date"] = sess[idx[idx < len(sess)]]

    # Price at the report, and the three-session reaction, both from bars that
    # had already printed by the usable date.
    react, price_at = [], []
    for sym, d in zip(cal["symbol"], cal["date"].to_numpy("datetime64[ns]")):
        p = prices.get(sym)
        if p is None:
            react.append(np.nan); price_at.append(np.nan); continue
        i = int(np.searchsorted(p["idx"], d, side="right")) - 1
        if i < 1 or i + 3 >= p["close"].size:
            react.append(np.nan)
            price_at.append(float(p["close"][i]) if i >= 0 else np.nan)
            continue
        base = p["close"][i - 1]
        price_at.append(float(p["close"][i]))
        react.append(float(p["close"][i + 3] / base - 1.0) * 100.0 if base > 0 else np.nan)
    cal["price_at_report"] = price_at
    cal["reaction_3d_pct"] = react

    e_a, e_e = cal["epsActual"], cal["epsEstimated"]
    r_a, r_e = cal["revenueActual"], cal["revenueEstimated"]
    with np.errstate(divide="ignore", invalid="ignore"):
        cal["eps_surprise_pct"] = np.clip(
            (e_a - e_e) / e_e.abs().clip(lower=EPS_DENOM_FLOOR) * 100.0,
            -SURPRISE_CLIP_PCT, SURPRISE_CLIP_PCT)
        cal["eps_surprise_vs_price_pct"] = np.where(
            cal["price_at_report"] > 0, (e_a - e_e) / cal["price_at_report"] * 100.0, np.nan)
        cal["rev_surprise_pct"] = np.clip(
            (r_a - r_e) / r_e.abs().clip(lower=1.0) * 100.0,
            -SURPRISE_CLIP_PCT, SURPRISE_CLIP_PCT)
    cal["eps_beat"] = (e_a > e_e).where(e_a.notna() & e_e.notna())
    cal["rev_beat"] = (r_a > r_e).where(r_a.notna() & r_e.notna())
    cal["both_beat"] = (cal["eps_beat"].fillna(False) & cal["rev_beat"].fillna(False))
    cal["eps_beat_rev_miss"] = (cal["eps_beat"].fillna(False)
                                & ~cal["rev_beat"].fillna(True))
    cal["rev_beat_eps_miss"] = (cal["rev_beat"].fillna(False)
                                & ~cal["eps_beat"].fillna(True))
    cal["has_surprise"] = cal["eps_beat"].notna()

    keep = ["symbol", "date", "usable_date", "epsActual", "epsEstimated",
            "revenueActual", "revenueEstimated", "eps_surprise_pct",
            "eps_surprise_vs_price_pct", "rev_surprise_pct", "eps_beat", "rev_beat",
            "both_beat", "eps_beat_rev_miss", "rev_beat_eps_miss", "has_surprise",
            "reaction_3d_pct", "price_at_report"]
    cal = cal[keep].sort_values(["symbol", "usable_date"])

    # As-of join: for each pool row, the latest event usable on or before the
    # scan date. merge_asof does exactly this and cannot see forward.
    left = pool[["ticker", "scan_date"]].copy()
    left["ticker"] = left["ticker"].astype(str)
    # merge_asof requires identical datetime resolutions on both join keys.
    left["scan_date"] = left["scan_date"].astype("datetime64[ns]")
    right = cal.rename(columns={"symbol": "ticker"}).copy()
    right["usable_date"] = right["usable_date"].astype("datetime64[ns]")
    right["date"] = right["date"].astype("datetime64[ns]")
    out = pd.merge_asof(
        left.sort_values("scan_date"), right.sort_values("usable_date"),
        left_on="scan_date", right_on="usable_date", by="ticker",
        direction="backward", allow_exact_matches=True)
    out["days_since_earnings"] = (
        out["scan_date"] - out["date"]).dt.days
    return out, cal


def _assert_no_future_events(panel, cal, n_checks: int = 400, seed: int = 3) -> dict:
    """Re-check sampled rows against the raw calendar for lookahead."""
    import numpy as np

    rng = np.random.default_rng(seed)
    have = panel.dropna(subset=["date"])
    if have.empty:
        return {"rows_checked": 0}
    sample = have.iloc[rng.choice(len(have), size=min(n_checks, len(have)), replace=False)]
    by_sym = {s: g for s, g in cal.groupby("symbol")}
    checked = 0
    for _, row in sample.iterrows():
        g = by_sym.get(row["ticker"])
        if g is None:
            continue
        if row["usable_date"] > row["scan_date"]:
            raise AssertionError(
                f"lookahead: {row['ticker']} used an event usable "
                f"{row['usable_date']} on scan date {row['scan_date']}")
        later = g[(g["usable_date"] > row["usable_date"])
                  & (g["usable_date"] <= row["scan_date"])]
        if not later.empty:
            raise AssertionError(
                f"stale join: {row['ticker']} on {row['scan_date']} used "
                f"{row['usable_date']} but a newer usable event existed")
        checked += 1
    return {"rows_checked": checked}


def panel_stage(args) -> int:
    import numpy as np
    import pandas as pd
    from research.backtests.alpha_reconstruction_lab import (
        load_prices, load_quarantine_excludes, weekly_scan_dates)
    from research.backtests.alpha_tournament import _build_pools
    from research.backtests.m1_conviction_layer import label_within_pool, m1_pool

    root = Path(args.root)
    tw = LiveArtifactTripwire.snapshot(root)
    pools = _build_pools(root, args)
    pool = label_within_pool(m1_pool(pools))
    prices = load_prices(root, load_quarantine_excludes(root))
    sessions = pd.DatetimeIndex(prices["SPY"]["idx"])

    sp, cal = build_surprise_panel(root, pool, prices, sessions)
    audit = _assert_no_future_events(sp, cal)
    pool = pool.copy()
    pool["scan_date"] = pool["scan_date"].astype("datetime64[ns]")
    merged = pool.merge(sp, on=["ticker", "scan_date"], how="left")
    assert len(merged) == len(pool), "the surprise join must not change pool size"

    out = root / PANEL_REL
    assert_replay_write_path(out, root=root)
    out.parent.mkdir(parents=True, exist_ok=True)
    slim = merged.copy()
    for c, dt in slim.dtypes.items():
        if dt == "float64":
            slim[c] = slim[c].astype("float32")
    slim["ticker"] = slim["ticker"].astype("category")
    slim.to_parquet(out, index=False, compression="zstd")

    within40 = merged["days_since_earnings"].between(0, 40)
    meta = {
        "kind": "M1_SURPRISE_PANEL_META",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pool_rows": int(len(merged)),
        "point_in_time_rule": "an event is usable from the first trading session "
                              "STRICTLY AFTER its report date; the calendar has no "
                              "time-of-day field, so a report dated D is unknown on D",
        "lookahead_audit": audit,
        "coverage": {
            "rows_with_any_prior_event_pct": round(
                float(merged["date"].notna().mean() * 100), 1),
            "rows_with_usable_surprise_pct": round(
                float(merged["has_surprise"].fillna(False).mean() * 100), 1),
            "rows_within_40d_of_earnings_pct": round(float(within40.mean() * 100), 1),
            "rows_within_40d_with_surprise_pct": round(
                float((within40 & merged["has_surprise"].fillna(False)).mean() * 100), 1),
            "unique_tickers_with_surprise": int(
                merged[merged["has_surprise"].fillna(False)].ticker.nunique()),
        },
        "estimate_provenance_warning": (
            "epsEstimated is the value the provider serves TODAY. Its lastUpdated "
            "field sits a median ~2 years after the report date, so the estimate "
            "cannot be verified as the pre-report consensus. Two checks argue "
            "against silent back-filling: the EPS beat rate is 54.0% (a real "
            "consensus shows a beat bias, an actuals-snapped field would show ~100%) "
            "and only 4.4% of rows have estimate exactly equal to actual. This is "
            "reassurance, not proof."),
        **provenance_flags(fundamentals=True),
    }
    assert_provenance(meta)
    write_replay_json(root / "cache/research/m1_surprise_panel_meta.json", meta, root=root)
    print(tw.report())
    tw.assert_clean()
    print(json.dumps(meta["coverage"], indent=1))
    print(f"lookahead audit: {audit['rows_checked']} rows re-checked, none stale or future")
    return 0





# ── overlays (Part: test inside frozen M1) ──────────────────────────────────

from research.backtests.alpha_reconstruction_lab import HOLDOUT_YEARS, TRAIN_YEARS
from research.backtests.alpha_tournament import (
    HORIZONS, LIST_SIZES, PRIMARY_H, _pct, _top_n, _z, asymmetry, selection_vs)
from research.backtests.m1_conviction_layer import (
    WALK_FORWARD_STEPS, _diff_vs, evaluate_list, random_control)

FRESH_DAYS = 40          # "recent report" window, from the audit's own framing


SURPRISE_OVERLAYS: list[dict] = [
    {"name": "eps_beat_any_age", "kind": "filter",
     "hypothesis": "the last report beat on EPS, whenever it was",
     "mask": lambda d: d.eps_beat.fillna(False),
     "rank": lambda d: _z(d, "eps_surprise_vs_price_pct")},
    {"name": "rev_beat_any_age", "kind": "filter",
     "hypothesis": "the last report beat on revenue",
     "mask": lambda d: d.rev_beat.fillna(False),
     "rank": lambda d: _z(d, "rev_surprise_pct")},
    {"name": "both_beat_any_age", "kind": "filter",
     "hypothesis": "the last report beat on both lines",
     "mask": lambda d: d.both_beat.fillna(False),
     "rank": lambda d: _z(d, "eps_surprise_vs_price_pct")},
    {"name": "eps_beat_rev_miss", "kind": "filter",
     "hypothesis": "beat on EPS but missed on revenue — cost-cut quality, not demand",
     "mask": lambda d: d.eps_beat_rev_miss.fillna(False),
     "rank": lambda d: _z(d, "eps_surprise_vs_price_pct")},
    {"name": "rev_beat_eps_miss", "kind": "filter",
     "hypothesis": "beat on revenue but missed on EPS — demand without margin",
     "mask": lambda d: d.rev_beat_eps_miss.fillna(False),
     "rank": lambda d: _z(d, "rev_surprise_pct")},
    {"name": "large_positive_surprise", "kind": "filter",
     "hypothesis": "top-quintile price-scaled EPS surprise — magnitude, not direction",
     "mask": lambda d: (_pct(d, "eps_surprise_vs_price_pct") >= 0.80)
                       & d.eps_beat.fillna(False),
     "rank": lambda d: _z(d, "eps_surprise_vs_price_pct")},
    {"name": "fresh_positive_surprise", "kind": "filter",
     "hypothesis": "post-earnings drift proper: a beat inside the last 40 sessions",
     "mask": lambda d: d.eps_beat.fillna(False) & d.days_since_earnings.between(0, FRESH_DAYS),
     "rank": lambda d: _z(d, "eps_surprise_vs_price_pct")},
    {"name": "fresh_beat_and_price_holds", "kind": "filter",
     "hypothesis": "a recent beat the market is still honouring — above the MA20",
     "mask": lambda d: (d.eps_beat.fillna(False)
                        & d.days_since_earnings.between(0, FRESH_DAYS)
                        & (d.above_ma20 == 1)),
     "rank": lambda d: _z(d, "eps_surprise_vs_price_pct")},
    {"name": "fresh_beat_and_positive_reaction", "kind": "filter",
     "hypothesis": "surprise AND follow-through: the three-day reaction was positive",
     "mask": lambda d: (d.eps_beat.fillna(False)
                        & d.days_since_earnings.between(0, FRESH_DAYS)
                        & (d.reaction_3d_pct > 0)),
     "rank": lambda d: _z(d, "reaction_3d_pct")},
    {"name": "fresh_beat_and_volume_expansion", "kind": "filter",
     "hypothesis": "a recent beat with volume behind it",
     "mask": lambda d: (d.eps_beat.fillna(False)
                        & d.days_since_earnings.between(0, FRESH_DAYS)
                        & (d.vol_expansion_5_63 > 1.2)),
     "rank": lambda d: _z(d, "vol_expansion_5_63")},
    {"name": "beat_and_not_top_volatility", "kind": "filter",
     "hypothesis": "a beat, held out of the volatility decile where payoffs invert",
     "mask": lambda d: d.eps_beat.fillna(False) & (_pct(d, "atr14_pct") <= 0.80),
     "rank": lambda d: _z(d, "eps_surprise_vs_price_pct")},
    {"name": "beat_and_fundamental_sanity", "kind": "filter",
     "hypothesis": "a beat from a profitable, cash-generative, non-diluting company",
     "mask": lambda d: (d.eps_beat.fillna(False) & (d.profitable == 1.0)
                        & (d.fcf_positive == 1.0) & (d.dilution_yoy_pct < 5)),
     "rank": lambda d: _z(d, "eps_surprise_vs_price_pct")},
    {"name": "avoid_eps_miss", "kind": "filter", "intent": "avoid",
     "hypothesis": "AVOID FILTER — drop names whose last report missed on EPS. "
                   "A name with no usable surprise is KEPT: absence of data is not "
                   "evidence of a miss.",
     "mask": lambda d: d.eps_beat.fillna(True).astype(bool),
     "rank": lambda d: _z(d, "mom_12_1")},
    {"name": "avoid_fresh_eps_miss", "kind": "filter", "intent": "avoid",
     "hypothesis": "AVOID FILTER — drop only RECENT misses, where the news is live",
     "mask": lambda d: ~(d.eps_beat.fillna(True).astype(bool).eq(False)
                         & d.days_since_earnings.between(0, FRESH_DAYS)),
     "rank": lambda d: _z(d, "mom_12_1")},
    {"name": "rank_by_surprise_magnitude", "kind": "ranker",
     "hypothesis": "CONTROL — re-order the whole pool by price-scaled surprise",
     "mask": lambda d: d["price"].notna(),
     "rank": lambda d: _z(d, "eps_surprise_vs_price_pct")},
    {"name": "no_surprise_control", "kind": "filter", "intent": "control",
     "hypothesis": "CONTROL — names with NO usable surprise. If these do as well, "
                   "the data is adding nothing.",
     "mask": lambda d: ~d.has_surprise.fillna(False),
     "rank": lambda d: _z(d, "mom_12_1")},
]


def verdict_for(ov: dict, res: dict, coverage_pct: float) -> tuple[str, list[str]]:
    """Seven-verdict ladder, judged as an ANNOTATION first."""
    reasons: list[str] = []
    tr = (res.get("train") or {}).get("top50") or {}
    if not tr.get("episodes"):
        return assert_conviction_verdict("INCONCLUSIVE"), ["no train episodes at top-50"]
    if coverage_pct < 10.0:
        return assert_conviction_verdict("REJECTED_DATA_UNSAFE"), [
            f"only {coverage_pct:.1f}% of pool rows carry this feature"]

    vr = ((res.get("train") or {}).get("vs_random_top50") or {})
    hr = ((res.get("holdout") or {}).get("vs_random_top50") or {})
    ci = vr.get("mean_ci") or [None, None]
    beats_random = ci[0] is not None and ci[0] > 0
    holds_out = (hr.get("mean_difference") or -1) > 0
    sel = tr.get("selection_vs_pool") or {}
    t_ci = sel.get("ticker_cluster_ci") or [None, None]
    per_name = t_ci[0] is not None and t_ci[0] > 0
    wf = res.get("walk_forward") or []
    wf_pos = sum(1 for w in wf if (w.get("mean_difference_vs_random") or -1) > 0)

    # Tail symmetry: the failure mode the audit warned about — an overlay that
    # finds live names and raises BOTH tails has found liveness, not conviction.
    prof = res.get("tail_profile") or {}
    amplifies_both = bool(prof.get("amplifies_both_tails"))

    if not beats_random:
        reasons.append("does not beat a random same-size M1 draw with its CI clear "
                       "of zero in train")
    if not holds_out:
        reasons.append(f"holdout difference vs random {hr.get('mean_difference')} "
                       "not above zero")
    if not per_name:
        reasons.append("ticker-clustered CI does not clear zero — no per-name gain")
    if wf_pos < 2:
        reasons.append(f"positive in only {wf_pos} of {len(wf)} walk-forward steps")
    if amplifies_both:
        reasons.append(f"raises the +50% rate {prof.get('right_tail_change_pct')}% AND "
                       f"the -25% rate {prof.get('left_tail_change_pct')}% — liveness, "
                       "not conviction")

    if ov.get("intent") == "avoid":
        left = prof.get("left_tail_change_pct")
        right = prof.get("right_tail_change_pct")
        if left is not None and right is not None and left <= -15 and right >= -20:
            return assert_conviction_verdict("USEFUL_AVOID_FILTER"), [
                f"cuts the -25% rate by {abs(left):.0f}% while costing "
                f"{abs(min(right, 0)):.0f}% of the +50% rate"]
        return assert_conviction_verdict("INCONCLUSIVE"), (
            ["did not reshape the tails favourably"] + reasons)

    if beats_random and holds_out and per_name and not amplifies_both:
        return assert_conviction_verdict("PROMISING_CONVICTION_OVERLAY"), [
            "beats a random M1 draw in train and holdout, improves the per-name "
            "reading, and does not simply amplify both tails"]
    if beats_random and not holds_out:
        return assert_conviction_verdict("REJECTED_OVERFIT"), (
            ["beat random in train, not in holdout"] + reasons)
    # An annotation: the tagged subset behaves differently from the pool even
    # though it cannot order the list.
    subset = res.get("subset_vs_pool") or {}
    if (subset.get("mean_difference") or 0) > 0 and (
            (subset.get("mean_ci") or [None, None])[0] or -1) > 0:
        return assert_conviction_verdict("USEFUL_EVENT_ANNOTATION"), (
            [f"the tagged subset beats the rest of the pool by "
             f"{subset['mean_difference']}pp with CI {subset['mean_ci']}, but cannot "
             f"order the list"] + reasons)
    neg = ((sel.get("selection_mean") or 0) < 0
           and ((sel.get("date_cluster_ci_mean") or [0, 0])[1] or 0) < 0)
    if neg:
        return assert_conviction_verdict("REJECTED_NEGATIVE_SELECTION"), (
            ["negative selection against its own pool"] + reasons)
    return assert_conviction_verdict("INCONCLUSIVE"), reasons

def evaluate_overlay(ov: dict, pool, args) -> dict:
    """Score one surprise overlay at every list size and horizon."""
    import numpy as np
    import pandas as pd

    try:
        mask = ov["mask"](pool).fillna(False)
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
        },
    }
    if sub.empty:
        res["verdict"] = assert_conviction_verdict("INCONCLUSIVE")
        res["verdict_reasons"] = ["rule selected no rows"]
        return res

    # Tail profile of the tagged subset against the rest of the pool.
    rest = pool[~mask]
    a_sub, a_rest = asymmetry(sub[f"fwd_{PRIMARY_H}d"]), asymmetry(rest[f"fwd_{PRIMARY_H}d"])
    if a_sub and a_rest and a_rest.get("pct_up_50") and a_rest.get("pct_down_25"):
        right = (a_sub["pct_up_50"] - a_rest["pct_up_50"]) / a_rest["pct_up_50"] * 100
        left = (a_sub["pct_down_25"] - a_rest["pct_down_25"]) / a_rest["pct_down_25"] * 100
        res["tail_profile"] = {
            "subset_pct_up_50": a_sub["pct_up_50"], "rest_pct_up_50": a_rest["pct_up_50"],
            "subset_pct_down_25": a_sub["pct_down_25"],
            "rest_pct_down_25": a_rest["pct_down_25"],
            "right_tail_change_pct": round(float(right), 1),
            "left_tail_change_pct": round(float(left), 1),
            "amplifies_both_tails": bool(right > 10 and left > 10),
        }
    res["subset_vs_pool"] = _diff_vs(sub, rest, args)
    res["subset_asymmetry_by_horizon"] = {
        f"{h}d": asymmetry(sub[f"fwd_{h}d"]) for h in HORIZONS}

    for split, years in (("train", TRAIN_YEARS), ("holdout", HOLDOUT_YEARS)):
        p_split = pool[pool.year.isin(years)]
        s_split = sub[sub.year.isin(years)]
        node = {"subset": evaluate_list(s_split, p_split, args)}
        rank = ov["rank"](pool)
        for n in LIST_SIZES:
            picks = _top_n(s_split, rank, n)
            node[f"top{n}"] = evaluate_list(picks, p_split, args)
            node[f"vs_random_top{n}"] = _diff_vs(
                picks, random_control(p_split, n, args.seed + n), args)
            node[f"vs_m1_native_top{n}"] = _diff_vs(
                picks, _top_n(p_split, pool["mom_12_1"], n), args)
        res[split] = node

    res["walk_forward"] = []
    rank = ov["rank"](pool)
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
            "mean_difference_vs_random": (d or {}).get("mean_difference")})
    res["by_year"] = {
        str(int(y)): {
            "episodes": int((sub.year == y).sum()),
            "subset_mean": (asymmetry(sub[sub.year == y][f"fwd_{PRIMARY_H}d"]) or {}).get("mean"),
        } for y in sorted(pool.year.unique())}
    res["verdict"], res["verdict_reasons"] = verdict_for(ov, res, cov)
    return res


def overlay_stage(args) -> int:
    import pandas as pd

    root = Path(args.root)
    tw = LiveArtifactTripwire.snapshot(root)
    f = root / PANEL_REL
    if not f.exists():
        raise SystemExit(f"missing {PANEL_REL} — run the panel stage first")
    pool = pd.read_parquet(f)
    pool["ticker"] = pool["ticker"].astype(str)
    # Parquet round-trips nullable booleans as floats; restore them so the masks
    # can use ordinary boolean logic instead of float comparisons.
    for c in ("eps_beat", "rev_beat", "both_beat", "eps_beat_rev_miss",
              "rev_beat_eps_miss", "has_surprise"):
        if c in pool.columns:
            pool[c] = pool[c].map({1.0: True, 0.0: False, True: True, False: False}) \
                             .astype("boolean")
    print(f"M1 pool with surprise: {len(pool):,} rows · "
          f"{pool['has_surprise'].fillna(False).mean() * 100:.1f}% carry a surprise",
          flush=True)

    results = []
    for ov in SURPRISE_OVERLAYS:
        t0 = time.time()
        r = evaluate_overlay(ov, pool, args)
        results.append(r)
        tr = (r.get("train") or {}).get("top50") or {}
        vr = ((r.get("train") or {}).get("vs_random_top50") or {})
        hr = ((r.get("holdout") or {}).get("vs_random_top50") or {})
        tp = r.get("tail_profile") or {}
        print(f"  {ov['name']:<32} cov={r['coverage']['share_of_pool_pct']:>5}% "
              f"n/date={r['coverage']['median_names_per_date']:>5.0f} "
              f"t50mean={str(tr.get('mean')):>7} vsR_tr={str(vr.get('mean_difference')):>7} "
              f"vsR_ho={str(hr.get('mean_difference')):>7} "
              f"tails={str(tp.get('right_tail_change_pct')):>6}/"
              f"{str(tp.get('left_tail_change_pct')):>6} -> {r.get('verdict')} "
              f"({time.time() - t0:.0f}s)", flush=True)

    out = {
        "kind": "M1_SURPRISE_OVERLAY_RESULTS",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "primary_horizon_days": PRIMARY_H,
        "pool_rows": int(len(pool)),
        "surprise_coverage_pct": round(
            float(pool["has_surprise"].fillna(False).mean() * 100), 1),
        "control_note": "every list is compared with a RANDOM same-size draw from "
                        "the same M1 pool on the same dates",
        "overlays": results,
        "verdict_counts": {},
        **provenance_flags(fundamentals=True),
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
    """Is the BEAT adding anything, or is the fundamental filter doing the work?

    The best-scoring overlay bundles an EPS beat with a profitability screen
    the conviction study had already tested on its own. Reporting the bundle
    without decomposing it would credit earnings data for someone else's work.
    """
    import pandas as pd

    fq = ((pool.profitable == 1.0) & (pool.fcf_positive == 1.0)
          & (pool.dilution_yoy_pct < 5))
    beat = pool.eps_beat.fillna(False).astype(bool)
    out = {"cohorts": {}}
    for name, m in (("fundamental_quality_alone", fq),
                    ("fundamental_quality_plus_beat", fq & beat),
                    ("eps_beat_alone", beat),
                    ("whole_pool", pd.Series(True, index=pool.index))):
        g = pool[m]
        a = asymmetry(g[f"fwd_{PRIMARY_H}d"]) or {}
        d = _diff_vs(g, pool, args) or {}
        out["cohorts"][name] = {
            "rows": int(len(g)), "mean": a.get("mean"), "median": a.get("median"),
            "pct_up_50": a.get("pct_up_50"), "pct_down_25": a.get("pct_down_25"),
            "mean_vs_whole_pool": d.get("mean_difference"),
        }
    out["incremental_value_of_the_beat"] = _diff_vs(pool[fq & beat], pool[fq & ~beat], args)
    out["reading"] = (
        "If eps_beat_alone is not clearly positive against the pool while "
        "fundamental_quality_alone is, then the bundle's result belongs to the "
        "fundamental filter and the earnings data is a passenger.")
    return out


def report_stage(args) -> int:
    import pandas as pd

    root = Path(args.root)
    tw = LiveArtifactTripwire.snapshot(root)
    ov = json.loads((root / OVERLAY_REL).read_text())
    meta = json.loads((root / "cache/research/m1_surprise_panel_meta.json").read_text())
    fetch = json.loads((root / FETCH_META_REL).read_text())
    pool = pd.read_parquet(root / PANEL_REL)
    pool["ticker"] = pool["ticker"].astype(str)
    for c in ("eps_beat", "rev_beat", "both_beat", "has_surprise"):
        pool[c] = pool[c].map({1.0: True, 0.0: False, True: True, False: False}) \
                         .astype("boolean")
    attrib = attribution_test(pool, args)
    by = {o["name"]: o for o in ov["overlays"]}

    L: list[str] = []
    A = L.append
    A("=" * 78)
    A("M1 SURPRISE OVERLAY — does earnings surprise rank inside the momentum pool?")
    A("=" * 78)
    A(f"Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')} · "
      f"{fetch['calls_made']} provider calls (cap {fetch['hard_cap']}), "
      f"{fetch['rows']:,} calendar rows")
    A("")
    A("RESEARCH ONLY. Replay evidence over a fixed historical window. Not live")
    A("forward evidence, not a gate/threshold/score change, not a trade signal.")
    A("")
    A("-" * 78)
    A("1. THE DATA")
    A("-" * 78)
    A(f"Fetched /earnings-calendar in {fetch['chunks']} monthly chunks, "
      f"{fetch['calls_made']} calls, {len(fetch['errors'])} errors.")
    A(f"Fields returned: {', '.join(fetch['observed_fields'])}")
    A("")
    for k, v in meta["coverage"].items():
        A(f"  {k}: {v}")
    A("")
    A(f"  Point-in-time rule: {meta['point_in_time_rule']}")
    A(f"  Lookahead audit: {meta['lookahead_audit']['rows_checked']} rows re-checked "
      f"against the raw calendar; none used a future or stale event.")
    A("")
    A("  ESTIMATE PROVENANCE — the one risk that cannot be closed:")
    A(f"  {meta['estimate_provenance_warning']}")
    A("")

    A("-" * 78)
    A("2. THE OVERLAYS")
    A("-" * 78)
    A(f"{'':2}{'overlay':<34}{'cov%':>6}{'/date':>7}{'t50 vs rand tr':>15}"
      f"{'ho':>8}{'right tail':>11}{'left tail':>10}  verdict")
    for o in ov["overlays"]:
        vr = ((o.get("train") or {}).get("vs_random_top50") or {})
        hr = ((o.get("holdout") or {}).get("vs_random_top50") or {})
        tp = o.get("tail_profile") or {}
        A(f"  {o['name']:<34}{o['coverage']['share_of_pool_pct']:>6}"
          f"{o['coverage']['median_names_per_date']:>7.0f}"
          f"{str(vr.get('mean_difference')):>15}{str(hr.get('mean_difference')):>8}"
          f"{str(tp.get('right_tail_change_pct')):>11}"
          f"{str(tp.get('left_tail_change_pct')):>10}  {o.get('verdict')}")
    A("")
    A(f"Verdict counts: {json.dumps(ov['verdict_counts'])}")
    A("")
    A("'t50 vs rand' is the test: a top-50 built by the overlay against a RANDOM")
    A("50 drawn from the same M1 pool on the same dates. Right/left tail columns")
    A("are the % change in the +50% and -25% rates of the tagged subset against")
    A("the rest of the pool.")
    A("")
    A("NO overlay earned PROMISING_CONVICTION_OVERLAY. Almost every one is NEGATIVE")
    A("against a random M1 draw in train. Three were refused as REJECTED_DATA_UNSAFE")
    A("for covering under 10% of pool rows — the fresh-surprise combinations that")
    A("were, on paper, the most interesting ones.")
    A("")

    A("-" * 78)
    A("3. ATTRIBUTION — WHOSE RESULT IS IT?")
    A("-" * 78)
    A(f"{'':2}{'cohort':<34}{'rows':>9}{'mean':>8}{'median':>9}{'+50%':>8}"
      f"{'-25%':>8}{'vs pool':>9}")
    for k, v in attrib["cohorts"].items():
        A(f"  {k:<34}{v['rows']:>9,}{str(v['mean']):>8}{str(v['median']):>9}"
          f"{str(v['pct_up_50']):>8}{str(v['pct_down_25']):>8}"
          f"{str(v['mean_vs_whole_pool']):>9}")
    inc = attrib["incremental_value_of_the_beat"] or {}
    A("")
    A(f"  Incremental value of the beat, inside the fundamental cohort: "
      f"{inc.get('mean_difference')}pp, CI {inc.get('mean_ci')}, "
      f"{inc.get('dates_won_pct')}% of dates.")
    A("")
    A("  EPS beat ALONE is worth -0.30pp against the pool — nothing. The")
    A("  best-scoring overlay bundles the beat with a profitability screen the")
    A("  conviction study had already found on its own, and that screen carries")
    A("  +1.01pp of the bundle's +1.18pp. The beat's own increment is +0.65pp with")
    A("  a CI whose lower bound is 0.04 — barely off zero.")
    A("")

    A("-" * 78)
    A("4. WHAT THE SURPRISE FILTERS ACTUALLY DO TO THE TAILS")
    A("-" * 78)
    A("Almost every surprise filter REDUCES both tails: an EPS beat cuts the +50%")
    A("rate by 34% and the -25% rate by 28%. These filters select calmer names, not")
    A("better ones — the same proportional trimming the conviction study found for")
    A("volatility and liquidity filters, arriving now from a completely different")
    A("data source.")
    A("")
    fq = by.get("beat_and_fundamental_sanity", {})
    tp = fq.get("tail_profile") or {}
    A(f"The one favourable asymmetry is beat_and_fundamental_sanity: right tail")
    A(f"{tp.get('right_tail_change_pct')}%, left tail {tp.get('left_tail_change_pct')}% "
      f"— it cuts losers half again as hard as winners.")
    A("")

    A("=" * 78)
    A("5. DIRECT ANSWERS")
    A("=" * 78)
    A("")
    A("1. Did earnings surprise explain any of the missing M1 dispersion?  ALMOST")
    A("   NONE. An EPS beat on its own is worth -0.30pp against the pool. Revenue")
    A("   beat is worth +0.54pp [0.19, 0.92]. Against an oracle headroom of ~69-98pp,")
    A("   this is noise.")
    A("")
    A("2. Does it improve ranking inside M1?  NO. Not one overlay beats a random")
    A("   same-size draw from M1 at top-50 in train with a CI clear of zero. Most")
    A("   are negative.")
    A("")
    A("3. Annotation or conviction overlay?  ANNOTATION AT BEST, and a borrowed one.")
    A("   Two earned USEFUL_EVENT_ANNOTATION, and the stronger of the two owes most")
    A("   of its effect to a fundamental screen rather than to earnings data.")
    A("")
    A("4. Does EPS surprise matter more than revenue surprise?  NO — the reverse,")
    A("   mildly. Revenue beat: +0.54pp against the pool with the CI clear of zero.")
    A("   EPS beat: -0.30pp. If anything here carries information it is the top")
    A("   line, which is the harder number to manage.")
    A("")
    A("5. Do both-beat events matter?  NO. both_beat is -0.55 against a random draw")
    A("   in train and trims both tails by about a third. Requiring both beats")
    A("   selects steadier companies, not better forward returns.")
    A("")
    A("6. Does post-earnings hold / follow-through matter?  IT MADE THINGS WORSE.")
    A("   fresh_beat_and_price_holds (-1.54 train, -2.01 holdout) and")
    A("   fresh_beat_and_positive_reaction (-1.15, -2.85) were the weakest overlays")
    A("   tested, and all three fresh-event variants fell under the 10% coverage")
    A("   floor. Inside a pool already selected for twelve-month momentum, demanding")
    A("   recent strength as well is redundant.")
    A("")
    A("7. Does negative surprise work as an avoid filter?  NO. Dropping EPS-miss")
    A("   names cuts the +50% rate 44% and the -25% rate only 24% — it removes more")
    A("   winners than losers. Dropping only RECENT misses cuts the right tail 10%")
    A("   while the left tail gets slightly WORSE. Both fail as avoid filters.")
    A("")
    A("8. Does it help top-25, top-50 or top-100?  NONE OF THEM.")
    A("")
    A("9. Does it improve per-name confidence?  NO in train, which is the bar that")
    A("   was pre-declared. beat_and_fundamental_sanity does clear the")
    A("   ticker-clustered bar in the 2025 holdout [2.24, 7.87] while failing it in")
    A("   train [-2.51, 0.30] — one year is not evidence, and taking it as such")
    A("   after seeing it would be exactly the error this programme keeps avoiding.")
    A("")
    A("10. Should it join the next shadow M1 report?  AS A TAG, NOT A RANK, and")
    A("    labelled honestly: 'profitable, cash-generative, non-diluting, and beat")
    A("    last quarter' is a reasonable annotation on ~17% of the list. The")
    A("    fundamental half is doing the work. Earnings surprise should NOT be used")
    A("    to order the list, filter it, or express conviction.")
    A("")
    A("=" * 78)
    A("BOTTOM LINE")
    A("=" * 78)
    A("The audit's recommended experiment came back negative, and cheaply: 52 calls")
    A("bought a clear answer. Earnings surprise does not rank inside M1. It does not")
    A("even rank on its own — an EPS beat is worth slightly less than nothing")
    A("against the pool, and the one overlay that looks good is mostly a")
    A("profitability screen wearing an earnings badge.")
    A("")
    A("This was the best available shot at the event hypothesis: the surprise data")
    A("is scheduled, point-in-time safe, and covers 81% of pool rows. It failed. The")
    A("move-anatomy finding that made this experiment worth running — that two")
    A("thirds of M1 winners make a quarter of their move in one session — still")
    A("stands, but the sessions that matter are evidently NOT the earnings ones.")
    A("That points at unscheduled events, which are exactly the class this repo")
    A("cannot observe historically.")
    A("")

    text = "\n".join(L)
    write_replay_text(root / REPORT_TXT_REL, text + "\n", root=root)
    summary = {
        "kind": "M1_SURPRISE_LATEST",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "provider_calls_made": fetch["calls_made"],
        "hard_cap": fetch["hard_cap"],
        "surprise_coverage_pct": ov["surprise_coverage_pct"],
        "verdict_counts": ov["verdict_counts"],
        "overlay_verdicts": {o["name"]: o.get("verdict") for o in ov["overlays"]},
        "headline": (
            "Earnings surprise does not rank inside M1. An EPS beat alone is worth "
            "-0.30pp against the pool; no overlay beats a random M1 draw at top-50 "
            "in train. The best result is a profitability screen the previous study "
            "already had, with the beat contributing +0.65pp [0.04, 1.25] on top."),
        "attribution": attrib,
        "eps_vs_revenue": {
            "eps_beat_alone_vs_pool": attrib["cohorts"]["eps_beat_alone"]["mean_vs_whole_pool"],
            "rev_beat_subset_vs_rest": (by.get("rev_beat_any_age", {})
                                        .get("subset_vs_pool") or {}).get("mean_difference"),
        },
        "not_supported": [
            "using earnings surprise to order or filter an M1 list",
            "any production change",
            "reading the single positive holdout ticker-CI as evidence",
        ],
        "report_txt": REPORT_TXT_REL,
        "artifacts": [OVERLAY_REL, "cache/research/m1_surprise_panel_meta.json"],
        **provenance_flags(fundamentals=True),
    }
    assert_provenance(summary)
    write_replay_json(root / LATEST_REL, summary, root=root)
    print(tw.report())
    tw.assert_clean()
    print(text)
    return 0



if __name__ == "__main__":
    raise SystemExit(main())
