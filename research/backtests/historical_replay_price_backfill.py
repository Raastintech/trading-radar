"""Stage 1C — backfill replay price bars for the survivorship-corrected universe.

Two sub-stages:

``fetch``
    Pull daily EOD bars for every symbol in the replay universe (survivors +
    delisted clean equities) into ``cache/replay_prices/``. Resumable through
    an append-only progress ledger, rate-limited, and self-aborting if the
    empty-response or error rate degrades mid-run. **Requires
    ``--execute-fetch``.**

``summarise``
    Zero-API pass over the progress ledger and the written parquets. Emits the
    backfill summary, the quarantine artifact the replay stages consume, and
    the human-readable log.

Writes ONLY:
    cache/replay_prices/{SYM}.parquet
    cache/replay_universe/replay_price_backfill_progress.jsonl
    cache/replay_universe/replay_price_backfill_summary.json
    cache/replay_universe/replay_price_quarantine.json
    logs/historical_replay_price_backfill_latest.txt

Touches nothing in cache/prices, cache/prices_deep, cache/backtest_prices,
cache/fundamentals, data/, or any scanner/dashboard artifact. No replay is run
here and no returns are computed — this stage produces universe prep only.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from pathlib import Path

from research.backtests.common import (
    LiveArtifactTripwire,
    ROOT,
    add_safety_args,
    enforce_call_cap,
    run_cli,
    require_execute_fetch,
    write_replay_json,
    write_replay_text,
)

PRICE_OUT_REL = "cache/replay_prices"
UNI_REL = "cache/replay_universe"
LOG_REL = "logs/historical_replay_price_backfill_latest.txt"

FETCH_FROM, FETCH_TO = "2020-01-01", "2026-09-05"
REPLAY_START, REPLAY_END = "2022-01-01", "2025-12-31"
DEFAULT_MAX_CALLS = 8500
RATE_PER_MIN = 600  # safety margin under the provider's 750 RPM ceiling
WORKERS = 10
MIN_BARS_REPLAY = 250  # below this, too thin for MA200 plus a 2022 start

# Production-identical suspect-jump constants (research_scanner.py:244-246).
# Kept in sync deliberately: the replay must apply the same data gate the live
# scanner applies, or it is not replaying the same system.
SUSPECT_JUMP_HI, SUSPECT_JUMP_LO, SUSPECT_WINDOW = 2.5, 0.4, 64

US_EXCHANGES = {
    "NASDAQ", "NYSE", "AMEX", "NYSE American", "NASDAQ Global Select",
    "NASDAQ Capital Market", "NASDAQ Global Market", "NYSE Arca", "CBOE",
}

NOTE = (
    "REPLAY-UNIVERSE PREP ONLY — not backtest evidence. No forward returns, "
    "scores, labels, or verdicts. Feeds no scanner, HC/EO, Alpha Focus, program "
    "verdict, routing, or live ledger."
)


def suspect(closes, window: int | None = None) -> bool:
    """Production's jump heuristic: a >2.5x or <0.4x day-over-day close move."""
    tail = closes[-window:] if window else closes
    for a, b in zip(tail, tail[1:]):
        if a > 0 and (b / a > SUSPECT_JUMP_HI or b / a < SUSPECT_JUMP_LO):
            return True
    return False


def _load_universe(root: Path) -> tuple[list[str], dict, set[str], list[str]]:
    """Survivors ∪ delisted clean equities, with delisting metadata."""
    uni_dir = root / UNI_REL
    man = json.loads((uni_dir / "replay_universe_manifest_clean_equities.json").read_text())
    rawrows = json.loads((uni_dir / "delisted_companies.json").read_text())["rows"]
    clean = set(man["clean_equity_symbols"])
    delisted_meta: dict[str, dict] = {}
    for r in rawrows:
        s = r.get("symbol")
        if (
            s in clean
            and r.get("exchange") in US_EXCHANGES
            and REPLAY_START <= str(r.get("delistedDate", "")) <= REPLAY_END
        ):
            delisted_meta.setdefault(
                s,
                {
                    "delisted_date": r["delistedDate"],
                    "exchange": r["exchange"],
                    "company": r.get("companyName"),
                },
            )
    survivors = {f.stem for f in (root / "cache" / "prices").glob("*.parquet")}
    collisions = sorted(set(delisted_meta) & survivors)
    universe = sorted(set(delisted_meta) | survivors)
    return universe, delisted_meta, survivors, collisions


def fetch_stage(args) -> int:
    root = Path(args.root)
    price_out = root / PRICE_OUT_REL
    uni_dir = root / UNI_REL
    progress = uni_dir / "replay_price_backfill_progress.jsonl"

    universe, delisted_meta, survivors, collisions = _load_universe(root)

    done: dict[str, dict] = {}
    if progress.exists():
        for line in progress.read_text().splitlines():
            try:
                r = json.loads(line)
                done[r["symbol"]] = r
            except Exception:
                pass
    todo = [s for s in universe if s not in done]

    print(
        f"universe={len(universe)}  survivors={len(survivors)}  "
        f"delisted={len(delisted_meta)}  collisions={collisions}"
    )
    print(f"already done={len(done)}  to fetch={len(todo)}")

    if not todo:
        print("nothing to fetch — ledger already complete (zero provider calls)")
        return 0

    cap = args.max_calls if args.max_calls is not None else DEFAULT_MAX_CALLS
    enforce_call_cap(len(todo), cap, what="replay price backfill")
    require_execute_fetch(args, planned_calls=len(todo), what="replay price backfill")

    tripwire = LiveArtifactTripwire.snapshot(root)

    import os  # noqa: PLC0415
    import requests  # noqa: PLC0415
    import pandas as pd  # noqa: PLC0415
    from dotenv import load_dotenv as _ld  # noqa: PLC0415

    ep_path = os.getenv("SNIPER_ENV_PATH", "").strip()
    if ep_path:
        _ld(ep_path, override=False)
    import core.config as cfg  # noqa: PLC0415
    from core.data_gatekeeper import get_gatekeeper  # noqa: PLC0415

    base = cfg.FMP_BASE_URL.rstrip("/")
    key = cfg.FMP_API_KEY
    gate = get_gatekeeper()
    endpoint = "/historical-price-eod/full"

    price_out.mkdir(parents=True, exist_ok=True)
    uni_dir.mkdir(parents=True, exist_ok=True)

    _lock = threading.Lock()
    _stamps: deque = deque()
    calls = 0

    def throttle():
        nonlocal calls
        while True:
            with _lock:
                now = time.time()
                while _stamps and now - _stamps[0] > 60:
                    _stamps.popleft()
                if len(_stamps) < RATE_PER_MIN:
                    _stamps.append(now)
                    calls += 1
                    return
                wait = 60 - (now - _stamps[0])
            time.sleep(max(wait, 0.01))

    _wlock = threading.Lock()

    def record(row):
        with _wlock:
            with progress.open("a") as fh:
                fh.write(json.dumps(row) + "\n")

    def source_of(s):
        if s in delisted_meta and s in survivors:
            return "collision"
        return "delisted" if s in delisted_meta else "survivor"

    stop = threading.Event()

    def fetch(sym):
        if stop.is_set():
            return None
        throttle()
        meta = {
            "symbol": sym,
            "source": source_of(sym),
            "delisted_date": delisted_meta.get(sym, {}).get("delisted_date"),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            r = requests.get(
                f"{base}{endpoint}",
                params={"symbol": sym, "from": FETCH_FROM, "to": FETCH_TO, "apikey": key},
                timeout=60,
            )
            gate.budget_consume(1)
            gate.log_endpoint(endpoint, saved=0, resp_bytes=len(r.content))
            if r.status_code != 200:
                meta.update(status="API_ERROR", http=r.status_code)
                record(meta)
                return meta
            data = r.json()
        except Exception as e:
            meta.update(status="API_ERROR", error=type(e).__name__)
            record(meta)
            return meta

        if not isinstance(data, list) or not data:
            # A rename or unresolvable symbol — drop it from the replay
            # universe. This is not an error and must not trip the abort rate.
            meta.update(status="EMPTY", rows=0)
            record(meta)
            return meta

        rows = sorted([x for x in data if x.get("date")], key=lambda x: x["date"])
        n_raw = len(rows)
        # Drop TRAILING zero-volume flat-OHLC padding only. The provider pads a
        # delisted name forward with synthetic flat bars; leaving them in would
        # fabricate a post-delisting return of exactly zero.
        pad = 0
        while rows:
            b = rows[-1]
            v = b.get("volume") or 0
            flat = b.get("open") == b.get("high") == b.get("low") == b.get("close")
            if v == 0 and flat:
                rows.pop()
                pad += 1
            else:
                break
        if not rows:
            meta.update(status="EMPTY_AFTER_PADDING", rows=0, padding_dropped=pad)
            record(meta)
            return meta

        df = pd.DataFrame(
            [
                {
                    "date": b["date"],
                    "open": float(b.get("open") or 0),
                    "high": float(b.get("high") or 0),
                    "low": float(b.get("low") or 0),
                    "close": float(b.get("close") or 0),
                    "volume": int(b.get("volume") or 0),
                }
                for b in rows
            ]
        )
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()
        try:
            df.to_parquet(price_out / f"{sym}.parquet", compression="snappy")
        except Exception as e:
            meta.update(status="WRITE_ERROR", error=str(e)[:120])
            record(meta)
            return meta

        closes = df["close"].tolist()
        traded = df[df["volume"] > 0]
        meta.update(
            status="OK",
            rows=len(df),
            rows_raw=n_raw,
            padding_dropped=pad,
            first_bar_date=str(df.index.min().date()),
            last_bar_date=str(df.index.max().date()),
            first_traded_bar_date=(str(traded.index.min().date()) if len(traded) else None),
            last_traded_bar_date=(str(traded.index.max().date()) if len(traded) else None),
            suspect_tail64=suspect(closes, SUSPECT_WINDOW),
            suspect_full_series=suspect(closes),
            too_few_bars=len(df) < MIN_BARS_REPLAY,
        )
        if meta["delisted_date"] and meta["last_traded_bar_date"]:
            meta["gap_lasttraded_minus_delisted_days"] = (
                date.fromisoformat(meta["last_traded_bar_date"])
                - date.fromisoformat(meta["delisted_date"])
            ).days
        record(meta)
        return meta

    t0 = time.time()
    stats: Counter = Counter()
    n = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(fetch, s): s for s in todo}
        for f in as_completed(futs):
            m = f.result()
            if m is None:
                continue
            stats[m["status"]] += 1
            n += 1
            if n % 500 == 0:
                el = time.time() - t0
                empty_rate = stats["EMPTY"] / n
                err_rate = stats["API_ERROR"] / n
                print(
                    f"  [{n}/{len(todo)}] {el:5.0f}s  ok={stats['OK']} "
                    f"empty={stats['EMPTY']} err={stats['API_ERROR']}  "
                    f"empty_rate={empty_rate:.1%} err_rate={err_rate:.1%}",
                    flush=True,
                )
                if n >= 500 and empty_rate > 0.15:
                    print("  !! STOPPING: empty rate > 15%")
                    stop.set()
                if n >= 500 and err_rate > 0.05:
                    print("  !! STOPPING: API error rate > 5%")
                    stop.set()

    print(f"\nfetched {n} in {time.time()-t0:.0f}s using {calls} calls")
    print("status:", dict(stats))
    print(tripwire.report())
    tripwire.assert_clean()
    return 0


def summarise_stage(args) -> int:
    """Zero-API: ledger + parquets -> summary, quarantine, log."""
    import numpy as np  # noqa: PLC0415
    import pandas as pd  # noqa: PLC0415

    root = Path(args.root)
    uni_dir = root / UNI_REL
    price_out = root / PRICE_OUT_REL
    progress = uni_dir / "replay_price_backfill_progress.jsonl"
    if not progress.exists():
        print(f"missing {progress} — run the fetch sub-stage first")
        return 2

    tripwire = LiveArtifactTripwire.snapshot(root)

    rows = []
    for line in progress.read_text().splitlines():
        try:
            rows.append(json.loads(line))
        except Exception:
            pass
    ok = [r for r in rows if r.get("status") == "OK"]
    empty = [r for r in rows if r.get("status", "").startswith("EMPTY")]
    errs = [r for r in rows if r.get("status") in ("API_ERROR", "WRITE_ERROR")]

    bars = sorted(r.get("rows", 0) for r in ok)
    pad_syms = [r for r in ok if r.get("padding_dropped")]
    pad_bars = sum(r.get("padding_dropped", 0) for r in ok)
    suspect_full = sorted(r["symbol"] for r in ok if r.get("suspect_full_series"))
    suspect_tail = [r for r in ok if r.get("suspect_tail64")]
    too_few = sorted(r["symbol"] for r in ok if r.get("too_few_bars"))
    delisted_ok = [r for r in ok if r.get("delisted_date")]
    gap30 = sorted(
        r["symbol"]
        for r in delisted_ok
        if (r.get("gap_lasttraded_minus_delisted_days") or 0) > 30
    )
    aligned5 = [
        r
        for r in delisted_ok
        if abs(r.get("gap_lasttraded_minus_delisted_days") or 999) <= 5
    ]

    # Corruption scan over the written parquets.
    hard_corrupt, near_zero = [], []
    for r in ok:
        f = price_out / f"{r['symbol']}.parquet"
        if not f.exists():
            continue
        try:
            c = pd.read_parquet(f, columns=["close"])["close"].to_numpy(float)
        except Exception:
            continue
        if c.size == 0:
            continue
        if not np.isfinite(c).all() or (c <= 0).any():
            hard_corrupt.append(r["symbol"])
        elif c.min() < 0.01:
            near_zero.append(r["symbol"])
    hard_corrupt.sort()
    near_zero.sort()

    reuse = sorted(set(gap30))
    clean_usable = len(ok) - len(set(hard_corrupt) | set(near_zero) | set(reuse))
    now = datetime.now(timezone.utc).isoformat()

    quarantine = {
        "kind": "REPLAY_PRICE_QUARANTINE",
        "research_only": True,
        "not_backtest_evidence": True,
        "note": (
            "Symbols whose replay price series must NOT be used as-is. Written "
            "to cache/replay_prices but flagged here; the replay must exclude or "
            "repair them explicitly rather than silently consuming them."
        ),
        "generated_at": now,
        "hard_corrupt_zero_negative_or_nan_close": hard_corrupt,
        "hard_corrupt_count": len(hard_corrupt),
        "near_zero_close_under_1c": near_zero,
        "near_zero_count": len(near_zero),
        "symbol_reuse_bars_past_delisting_over_30d": reuse,
        "symbol_reuse_count": len(reuse),
        "suspect_full_series_symbols_count": len(suspect_full),
        "suspect_tail64_count": len(suspect_tail),
        "guidance": [
            "HARD-CORRUPT: exclude from the replay universe entirely.",
            "NEAR-ZERO (<$0.01): sub-penny series produce meaningless pct returns; "
            "exclude or floor-gate by price, consistent with the scanner's own "
            "$2 minimum.",
            "SYMBOL REUSE: key by symbol + active date range; truncate the series "
            "at last_traded_bar_date consistent with the delisting event, or exclude.",
            "suspect_full_series is a multi-year jump scan and flags legitimate "
            "microcap volatility; it is NOT by itself a corruption verdict. The "
            "production tail-64 rule remains the scanner's own gate.",
        ],
    }
    write_replay_json(uni_dir / "replay_price_quarantine.json", quarantine, root=root)

    summary = {
        "kind": "REPLAY_PRICE_BACKFILL_SUMMARY",
        "research_only": True,
        "not_backtest_evidence": True,
        "note": NOTE,
        "generated_at": now,
        "replay_window": {"start": REPLAY_START, "end": REPLAY_END},
        "fetch_window": {"from": FETCH_FROM, "to": FETCH_TO},
        "counts": {
            "requested": len(rows),
            "written_ok": len(ok),
            "empty_no_data": len(empty),
            "api_or_write_errors": len(errs),
            "padding_dropped_symbols": len(pad_syms),
            "padding_dropped_bars_total": pad_bars,
            "suspect_tail64": len(suspect_tail),
        },
        "coverage": {
            "bars_min": bars[0] if bars else 0,
            "bars_median": bars[len(bars) // 2] if bars else 0,
            "bars_max": bars[-1] if bars else 0,
        },
        "delisted": {
            "written": len(delisted_ok),
            "aligned_within_5d": len(aligned5),
            "gap_over_30d": len(gap30),
        },
        "empty_symbols": sorted(r["symbol"] for r in empty),
        "suspect_full_series_symbols": suspect_full,
        "too_few_bars_symbols": too_few,
        "delisted_gap_over_30d": gap30,
        "quarantine": {
            "artifact": "cache/replay_universe/replay_price_quarantine.json",
            "hard_corrupt_zero_neg_nan": len(hard_corrupt),
            "near_zero_sub_penny": len(near_zero),
            "symbol_reuse_over_30d": len(reuse),
            "clean_usable_after_quarantine": clean_usable,
        },
    }
    write_replay_json(uni_dir / "replay_price_backfill_summary.json", summary, root=root)

    txt = [
        "REPLAY PRICE BACKFILL — Stage 1C",
        "=" * 60,
        f"generated_at        : {now}",
        "REPLAY-UNIVERSE PREP ONLY — NOT backtest evidence.",
        "",
        f"replay window       : {REPLAY_START} .. {REPLAY_END}",
        f"fetch window        : {FETCH_FROM} .. {FETCH_TO}",
        "",
        f"requested           : {len(rows):,}",
        f"written OK          : {len(ok):,}",
        f"empty / no-data     : {len(empty):,}",
        f"errors              : {len(errs):,}",
        "",
        f"padding dropped     : {len(pad_syms):,} symbols / {pad_bars:,} bars",
        f"suspect tail-64     : {len(suspect_tail):,}",
        f"suspect full series : {len(suspect_full):,}",
        f"too few bars (<{MIN_BARS_REPLAY}): {len(too_few):,}",
        "",
        f"delisted written    : {len(delisted_ok):,}",
        f"  aligned <=5d      : {len(aligned5):,} / {len(delisted_ok):,}",
        f"  gap >30d          : {len(gap30):,}",
        "",
        "QUARANTINE",
        "-" * 60,
        f"hard corrupt (zero/neg/NaN close) : {len(hard_corrupt)}",
        f"sub-penny (<$0.01) series         : {len(near_zero)}",
        f"symbol reuse (>30d past delisting): {len(reuse)}",
        f"clean usable                      : {clean_usable:,}",
        "",
        "live cache, live ledgers, scanner artifacts, dashboard: UNCHANGED",
    ]
    write_replay_text(root / LOG_REL, "\n".join(txt) + "\n", root=root)
    print("\n".join(txt))
    print()
    print(tripwire.report())
    tripwire.assert_clean()
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="stage", required=True)

    f = sub.add_parser("fetch", help="backfill replay price bars (PROVIDER CALLS)")
    add_safety_args(f, fetches=True)
    f.set_defaults(func=fetch_stage)

    s = sub.add_parser("summarise", help="ledger -> summary + quarantine (zero API)")
    add_safety_args(s)
    s.set_defaults(func=summarise_stage)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run_cli(args.func, args)


if __name__ == "__main__":
    sys.exit(main())
