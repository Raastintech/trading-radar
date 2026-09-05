"""Stage 3 pass-1 — fetch point-in-time statements + market cap for the replay.

Fetches quarterly income / balance / cash-flow statements and the historical
market-cap series for the board-reaching tickers only (the names the price-only
replay actually surfaced), not the whole universe — roughly 2,700 tickers at
4 calls each rather than 7,800.

**Point-in-time requirement:** every statement row must carry ``acceptedDate``.
Rows without it are dropped rather than kept, because a statement whose filing
date is unknown cannot be placed on a historical timeline without leaking
future information into the replay.

Requires ``--execute-fetch``. Resumable through an append-only ledger,
rate-limited, and self-aborting above a 10% failure rate.

Writes ONLY:
    cache/replay_fundamentals/{SYM}.json
    cache/replay_market_cap/{SYM}.parquet
    cache/replay_universe/replay_fundamental_fetch_progress.jsonl

Never touches cache/fundamentals, cache/prices*, data/, or any live artifact.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from research.backtests.common import (
    LiveArtifactTripwire,
    ROOT,
    add_safety_args,
    enforce_call_cap,
    require_execute_fetch,
    run_cli,
)

EPISODES_REL = "cache/research/historical_replay_episodes.jsonl"
FUND_REL = "cache/replay_fundamentals"
MC_REL = "cache/replay_market_cap"
LEDGER_REL = "cache/replay_universe/replay_fundamental_fetch_progress.jsonl"

FETCH_FROM, FETCH_TO = "2020-01-01", "2026-09-05"
DEFAULT_MAX_CALLS = 11500
RATE_PER_MIN = 600
WORKERS = 10
CALLS_PER_TICKER = 4  # income + balance + cashflow + historical market cap


def run(args) -> int:
    root = Path(args.root)
    ep_path = root / EPISODES_REL
    if not ep_path.exists():
        print(f"missing {EPISODES_REL} — run stage 2 (replay) first")
        return 2

    eps = [json.loads(l) for l in ep_path.read_text().splitlines() if l.strip()]
    tickers = sorted({e["ticker"] for e in eps})
    ledger = root / LEDGER_REL

    done: set[str] = set()
    if ledger.exists():
        for line in ledger.read_text().splitlines():
            try:
                done.add(json.loads(line)["symbol"])
            except Exception:
                pass
    todo = [t for t in tickers if t not in done]
    planned = len(todo) * CALLS_PER_TICKER

    print(f"board-reaching tickers: {len(tickers):,}  -> {planned:,} calls")
    print(f"already done {len(done)}  to fetch {len(todo)}")

    if not todo:
        print("nothing to fetch — ledger already complete (zero provider calls)")
        return 0

    cap = args.max_calls if args.max_calls is not None else DEFAULT_MAX_CALLS
    enforce_call_cap(planned, cap, what="replay fundamental fetch")
    require_execute_fetch(args, planned_calls=planned, what="replay fundamental fetch")

    tripwire = LiveArtifactTripwire.snapshot(root)

    import os  # noqa: PLC0415
    import requests  # noqa: PLC0415
    import pandas as pd  # noqa: PLC0415
    from dotenv import load_dotenv as _ld  # noqa: PLC0415

    ep_env = os.getenv("SNIPER_ENV_PATH", "").strip()
    if ep_env:
        _ld(ep_env, override=False)
    import core.config as cfg  # noqa: PLC0415
    from core.data_gatekeeper import get_gatekeeper  # noqa: PLC0415

    base = cfg.FMP_BASE_URL.rstrip("/")
    key = cfg.FMP_API_KEY
    gate = get_gatekeeper()

    fund_dir = root / FUND_REL
    mc_dir = root / MC_REL
    fund_dir.mkdir(parents=True, exist_ok=True)
    mc_dir.mkdir(parents=True, exist_ok=True)
    ledger.parent.mkdir(parents=True, exist_ok=True)

    _lk = threading.Lock()
    _st: deque = deque()
    calls = 0

    def throttle():
        nonlocal calls
        while True:
            with _lk:
                now = time.time()
                while _st and now - _st[0] > 60:
                    _st.popleft()
                if len(_st) < RATE_PER_MIN:
                    _st.append(now)
                    calls += 1
                    return
                w = 60 - (now - _st[0])
            time.sleep(max(w, 0.01))

    _wl = threading.Lock()

    def record(r):
        with _wl:
            with ledger.open("a") as fh:
                fh.write(json.dumps(r) + "\n")

    def get(path, **params):
        throttle()
        p = dict(params)
        p["apikey"] = key
        try:
            r = requests.get(f"{base}{path}", params=p, timeout=60)
            gate.budget_consume(1)
            gate.log_endpoint(path, saved=0, resp_bytes=len(r.content))
            if r.status_code != 200:
                return None, f"HTTP{r.status_code}"
            return r.json(), None
        except Exception as e:
            gate.budget_consume(1)
            return None, type(e).__name__

    stop = threading.Event()

    def fetch(sym):
        if stop.is_set():
            return None
        meta = {"symbol": sym, "fetched_at": datetime.now(timezone.utc).isoformat()}
        out = {"ticker": sym}
        errs: list[str] = []
        kept: Counter = Counter()
        dropped: Counter = Counter()
        for path, k in (
            ("/income-statement", "income"),
            ("/balance-sheet-statement", "balance"),
            ("/cash-flow-statement", "cashflow"),
        ):
            d, e = get(path, symbol=sym, period="quarter", limit=32)
            if e:
                errs.append(f"{k}:{e}")
                out[k] = []
                continue
            rows = d if isinstance(d, list) else []
            # PIT REQUIREMENT: acceptedDate mandatory. Drop rows without it.
            good = [r for r in rows if r.get("acceptedDate")]
            dropped[k] = len(rows) - len(good)
            kept[k] = len(good)
            out[k] = good

        d, e = get(
            "/historical-market-capitalization",
            symbol=sym,
            limit=5000,
            **{"from": FETCH_FROM, "to": FETCH_TO},
        )
        mc_rows = 0
        if e:
            errs.append(f"mktcap:{e}")
        elif isinstance(d, list) and d:
            try:
                m = pd.DataFrame(
                    [
                        {"date": x["date"], "marketCap": float(x["marketCap"])}
                        for x in d
                        if x.get("date") and x.get("marketCap") is not None
                    ]
                )
                if len(m):
                    m["date"] = pd.to_datetime(m["date"])
                    m = m.set_index("date").sort_index()
                    m.to_parquet(mc_dir / f"{sym}.parquet", compression="snappy")
                    mc_rows = len(m)
            except Exception as ex:
                errs.append(f"mktcap_write:{type(ex).__name__}")

        if any(out.get(k) for k in ("income", "balance", "cashflow")):
            (fund_dir / f"{sym}.json").write_text(json.dumps(out))
        meta.update(
            status="OK" if not errs else ("PARTIAL" if (mc_rows or kept) else "FAILED"),
            errors=errs or None,
            quarters=dict(kept),
            rows_dropped_no_acceptedDate=dict(dropped),
            mktcap_rows=mc_rows,
            oldest_income=(out["income"][-1].get("acceptedDate") if out.get("income") else None),
        )
        record(meta)
        return meta

    t0 = time.time()
    stats: Counter = Counter()
    n = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(fetch, t): t for t in todo}
        for f in as_completed(futs):
            m = f.result()
            if m is None:
                continue
            stats[m["status"]] += 1
            n += 1
            if n % 250 == 0:
                fr = stats["FAILED"] / n
                print(
                    f"  [{n}/{len(todo)}] {time.time()-t0:.0f}s calls={calls} "
                    f"ok={stats['OK']} partial={stats['PARTIAL']} "
                    f"failed={stats['FAILED']} fail_rate={fr:.1%}",
                    flush=True,
                )
                if n >= 250 and fr > 0.10:
                    print("  !! STOPPING: failure rate >10%")
                    stop.set()

    print(f"\ndone {time.time()-t0:.0f}s  calls={calls}  {dict(stats)}")
    print(tripwire.report())
    tripwire.assert_clean()
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    add_safety_args(p, fetches=True)
    return p


def main(argv: list[str] | None = None) -> int:
    return run_cli(run, build_parser().parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
