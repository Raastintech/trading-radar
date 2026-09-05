"""Stage 1A/1B — build the survivorship-corrected replay universe.

Two sub-stages, both writing only into ``cache/replay_universe/``:

``harvest``
    Page the provider's ``/delisted-companies`` endpoint into the replay
    namespace. This is the survivorship correction: without it the replay
    universe is only the names that still trade today. **Requires
    ``--execute-fetch``.**

``filter``
    Zero-API local pass that separates genuine common equity from ETPs, funds,
    warrants/units/rights, preferreds, and mutual funds, then emits the
    clean-equity manifest that stage 1C backfills prices for.

The completed 2022-2025 run's manifest is preserved at
``cache/replay_universe/replay_universe_manifest_clean_equities.json``
(clean-equity hash ``d1b346ea7893…``, 2,123 delisted clean equities against a
5,887-name survivor pool). Re-running ``filter`` regenerates the manifest from
the raw harvest; note that the original run applied one interactive refinement
pass between ``filter`` and the stored manifest which was not captured as code,
so a re-derivation can differ marginally from the stored hash. The stored
manifest is authoritative for the recorded results.

RESEARCH ONLY. Universe prep carries no forward returns, scores, or verdicts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from research.backtests.common import (
    ROOT,
    add_safety_args,
    run_cli,
    assert_replay_write_path,
    enforce_call_cap,
    require_execute_fetch,
    write_replay_json,
)

OUTDIR_REL = "cache/replay_universe"

# Harvest keeps 2021 so the exclusion decision stays auditable; the replay
# window itself starts 2022-01-01 (see common.EXCLUDED_YEARS_REASON).
HARVEST_START = "2021-01-01"
HARVEST_END = "2025-12-31"

US_EXCHANGES = {
    "NASDAQ", "NYSE", "AMEX", "NYSE American", "NASDAQ Global Select",
    "NASDAQ Capital Market", "NASDAQ Global Market", "NYSE Arca", "CBOE",
}

NOTE = (
    "REPLAY-UNIVERSE PREP ONLY — not backtest evidence. Contains no forward "
    "returns, scores, labels, or verdicts. Does not feed the scanner, HC/EO, "
    "Alpha Focus, program verdicts, routing, or any live ledger."
)

ENDPOINT = "/delisted-companies"
DEFAULT_MAX_PAGES = 125


# ── sub-stage: harvest (provider calls) ─────────────────────────────────────


def harvest(args) -> int:
    """Page /delisted-companies into the replay namespace."""
    root = Path(args.root)
    outdir = root / OUTDIR_REL

    # Plan first, authorise second — nothing is fetched before this gate.
    planned = args.max_pages
    enforce_call_cap(planned, args.max_calls, what="delisted-company harvest")
    require_execute_fetch(args, planned_calls=planned, what="delisted-company harvest")

    from dotenv import load_dotenv as _ld  # noqa: PLC0415
    import os  # noqa: PLC0415
    import requests  # noqa: PLC0415

    ep = os.getenv("SNIPER_ENV_PATH", "").strip()
    if ep:
        _ld(ep, override=False)
    import core.config as cfg  # noqa: PLC0415
    from core.data_gatekeeper import get_gatekeeper  # noqa: PLC0415

    base = cfg.FMP_BASE_URL.rstrip("/")
    key = cfg.FMP_API_KEY
    gate = get_gatekeeper()
    calls = 0

    def page(n: int):
        nonlocal calls
        if calls >= args.max_pages:
            raise RuntimeError(f"page cap {args.max_pages} reached at page {n}")
        r = requests.get(
            f"{base}{ENDPOINT}",
            params={"page": n, "limit": 100, "apikey": key},
            timeout=60,
        )
        calls += 1
        gate.budget_consume(1)
        gate.log_endpoint(ENDPOINT, saved=0, resp_bytes=len(r.content))
        if r.status_code != 200:
            print(f"  page {n}: HTTP {r.status_code} — stopping")
            return None
        try:
            d = r.json()
        except Exception:
            return None
        return d if isinstance(d, list) else None

    print("Harvesting /delisted-companies …")
    raw: list[dict] = []
    n = 0
    while True:
        d = page(n)
        if not d:
            print(f"  page {n}: empty — end of pagination ({calls} calls)")
            break
        raw.extend(d)
        if n % 20 == 0:
            dds = [str(x.get("delistedDate")) for x in d if x.get("delistedDate")]
            print(
                f"  page {n:3d}: +{len(d):3d} rows  "
                f"({min(dds) if dds else '?'} .. {max(dds) if dds else '?'})"
            )
        n += 1

    print(f"\nraw rows harvested: {len(raw):,}  over {n} non-empty pages, {calls} calls")

    by_sym: dict[str, list[dict]] = defaultdict(list)
    for r in raw:
        if r.get("symbol"):
            by_sym[r["symbol"]].append(r)
    exact_dupes = sum(len(v) - 1 for v in by_sym.values() if len(v) > 1)
    reused = {s: v for s, v in by_sym.items() if len(v) > 1}
    exch = Counter(r.get("exchange") for r in raw)
    dds = sorted(str(r.get("delistedDate")) for r in raw if r.get("delistedDate"))
    missing_dd = sum(1 for r in raw if not r.get("delistedDate"))
    today = datetime.now(timezone.utc).date().isoformat()
    future = [r for r in raw if str(r.get("delistedDate", "")) > today]

    us = [r for r in raw if r.get("exchange") in US_EXCHANGES]
    window = [
        r for r in us if HARVEST_START <= str(r.get("delistedDate", "")) <= HARVEST_END
    ]
    post = [r for r in us if str(r.get("delistedDate", "")) > HARVEST_END]
    byyear = Counter(str(r.get("delistedDate"))[:4] for r in window)

    outdir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()

    write_replay_json(
        outdir / "delisted_companies.json",
        {
            "kind": "REPLAY_UNIVERSE_DELISTED_RAW",
            "research_only": True,
            "not_backtest_evidence": True,
            "note": NOTE,
            "source_endpoint": ENDPOINT,
            "generated_at": now,
            "pages_harvested": n,
            "api_calls": calls,
            "row_count": len(raw),
            "rows": raw,
        },
        root=root,
    )

    canon = sorted(
        f"{r.get('symbol')}|{r.get('delistedDate')}|{r.get('exchange')}" for r in window
    )
    uni_hash = hashlib.sha256("\n".join(canon).encode()).hexdigest()

    write_replay_json(
        outdir / "delisted_companies_summary.json",
        {
            "kind": "REPLAY_UNIVERSE_DELISTED_SUMMARY",
            "research_only": True,
            "not_backtest_evidence": True,
            "note": NOTE,
            "generated_at": now,
            "api_calls": calls,
            "pages_harvested": n,
            "raw_rows": len(raw),
            "unique_symbols": len(by_sym),
            "repeated_symbol_rows": exact_dupes,
            "symbol_reuse_examples": {s: v for s, v in list(reused.items())[:20]},
            "missing_delisted_date": missing_dd,
            "future_dated_delistings": len(future),
            "delisted_date_span": {"first": dds[0], "last": dds[-1]} if dds else {},
            "exchange_counts": dict(exch.most_common()),
            "us_exchanges_used": sorted(US_EXCHANGES),
            "us_rows": len(us),
            "harvest_window": {"start": HARVEST_START, "end": HARVEST_END},
            "us_delisted_in_window": len(window),
            "us_delisted_after_window": len(post),
            "by_delisting_year": dict(sorted(byyear.items())),
            "window_symbols_hash_sha256": uni_hash,
        },
        root=root,
    )
    print(f"\nharvest complete — {calls} calls, hash {uni_hash}")
    return 0


# ── sub-stage: filter (zero API) ────────────────────────────────────────────

ETP_SPONSOR = re.compile(
    r"\b(ishares|invesco|spdr|direxion|proshares|etracs|vaneck|van eck|wisdomtree|"
    r"global x|xtrackers|first trust|pimco etf|schwab strategic|flexshares|"
    r"amplify etf|defiance etf|graniteshares|ipath|elements|ubs etracs|"
    r"janus henderson etf|simplify exchange)\b",
    re.I,
)
ETP_WORD = re.compile(r"\b(etf|etn|exchange[- ]traded)\b", re.I)
FUND_WORD = re.compile(
    r"\b(fund|portfolio|index fund|money market|municipal (income|bond))\b", re.I
)
DERIV_WORD = re.compile(
    r"\b(warrant|warrants|right|rights|unit|units|"
    r"depositary (share|receipt)|preferred|preference)\b",
    re.I,
)
SUFFIX_DERIV = re.compile(r"[-.](UN|WT|WS|RT|U|W|R)$", re.I)
SUFFIX_PREF = re.compile(r"[-.]P[A-Z]?$", re.I)
FIVE_UNIT = re.compile(r"^[A-Z]{4}U$")
FIVE_WARRANT = re.compile(r"^[A-Z]{4}W$")
FIVE_RIGHT = re.compile(r"^[A-Z]{4}R$")
FIVE_MUTUAL = re.compile(r"^[A-Z]{4}X$")
SPAC_NAME = re.compile(r"\b(acquisition|acquisitions)\b", re.I)


def classify(row: dict) -> tuple[str, list[str]]:
    """Bucket one delisted listing as CLEAN / EXCLUDED / UNCERTAIN.

    SPAC *common* is kept — it is genuine listed equity; only SPAC units,
    warrants and rights are dropped by suffix. A bare 'Trust' is UNCERTAIN
    rather than excluded, because REITs are real equities.
    """
    sym = (row.get("symbol") or "").upper()
    nm = row.get("companyName") or ""
    why: list[str] = []
    if ETP_SPONSOR.search(nm):
        why.append("etp_sponsor")
    if ETP_WORD.search(nm):
        why.append("etf_etn_name")
    if FUND_WORD.search(nm):
        why.append("fund_name")
    if DERIV_WORD.search(nm):
        why.append("derivative_name")
    if SUFFIX_DERIV.search(sym):
        why.append("suffix_class")
    if SUFFIX_PREF.search(sym):
        why.append("suffix_preferred")
    if FIVE_UNIT.match(sym):
        why.append("5char_unit_U")
    if FIVE_WARRANT.match(sym):
        why.append("5char_warrant_W")
    if FIVE_RIGHT.match(sym):
        why.append("5char_right_R")
    if FIVE_MUTUAL.match(sym):
        why.append("5char_mutualfund_X")
    if why:
        return "EXCLUDED", why
    if re.search(r"\btrust\b", nm, re.I):
        return "UNCERTAIN", ["bare_trust_name_could_be_reit"]
    if re.match(r"^[A-Z]{5}$", sym):
        return "UNCERTAIN", ["five_char_symbol_unclassified"]
    return "CLEAN", []


def filter_equities(args) -> int:
    """Local ETP/fund/derivative filter over the harvested raw list. Zero API."""
    root = Path(args.root)
    outdir = root / OUTDIR_REL
    raw_path = outdir / "delisted_companies.json"
    if not raw_path.exists():
        print(f"missing {raw_path.relative_to(root)} — run the harvest sub-stage first")
        return 2
    raw = json.loads(raw_path.read_text())["rows"]

    win = [
        r
        for r in raw
        if r.get("exchange") in US_EXCHANGES
        and HARVEST_START <= str(r.get("delistedDate", "")) <= HARVEST_END
    ]
    seen, dedup = set(), []
    for r in win:
        k = (r.get("symbol"), r.get("delistedDate"), r.get("exchange"))
        if k not in seen:
            seen.add(k)
            dedup.append(r)

    buckets: dict[str, list[dict]] = {"CLEAN": [], "EXCLUDED": [], "UNCERTAIN": []}
    reasons: Counter = Counter()
    for r in dedup:
        b, why = classify(r)
        r = dict(r)
        r["_filter_reasons"] = why
        buckets[b].append(r)
        for w in why:
            reasons[w] += 1

    clean, excl, unc = buckets["CLEAN"], buckets["EXCLUDED"], buckets["UNCERTAIN"]
    spac_common = [r for r in clean if SPAC_NAME.search(r.get("companyName") or "")]

    print(f"raw delisted rows (full list)   : {len(raw):,}")
    print(f"US harvest-window rows          : {len(win):,}")
    print(f"deduped symbols                 : {len(dedup):,}")
    print(f"  EXCLUDED (ETP/fund/derivative): {len(excl):,}")
    print(f"  UNCERTAIN                     : {len(unc):,}")
    print(f"  CLEAN EQUITY                  : {len(clean):,}")
    print(f"    SPAC-common kept            : {len(spac_common):,}")
    print("\nexclusion reasons:")
    for k, v in reasons.most_common():
        print(f"   {k:28} {v:5}")

    byyear = Counter(str(r["delistedDate"])[:4] for r in clean)
    byexch = Counter(r["exchange"] for r in clean)
    canon = sorted(f"{r['symbol']}|{r['delistedDate']}|{r['exchange']}" for r in clean)
    clean_hash = hashlib.sha256("\n".join(canon).encode()).hexdigest()
    now = datetime.now(timezone.utc).isoformat()

    caveats = [
        "SPAC common stock is KEPT (genuine listed equity); only SPAC units/"
        "warrants/rights are excluded by suffix.",
        "'Trust' alone is UNCERTAIN, not excluded — REITs are real equities.",
        "5-char symbols ending X are treated as mutual funds; unclassified "
        "5-char symbols are UNCERTAIN rather than dropped.",
        "Raw list is preserved unmodified in delisted_companies.json.",
    ]

    write_replay_json(
        outdir / "delisted_equity_filter_summary.json",
        {
            "kind": "REPLAY_UNIVERSE_DELISTED_EQUITY_FILTER",
            "research_only": True,
            "not_backtest_evidence": True,
            "note": NOTE,
            "api_calls": 0,
            "generated_at": now,
            "harvest_window": {"start": HARVEST_START, "end": HARVEST_END},
            "raw_rows_full_list": len(raw),
            "us_window_rows": len(win),
            "deduped_symbols": len(dedup),
            "counts": {
                "clean_equity": len(clean),
                "excluded_etp_fund_derivative": len(excl),
                "uncertain": len(unc),
                "spac_common_kept": len(spac_common),
            },
            "exclusion_reason_counts": dict(reasons),
            "clean_by_year": dict(sorted(byyear.items())),
            "clean_by_exchange": dict(byexch.most_common()),
            "clean_equity_hash_sha256": clean_hash,
            "hash_basis": "sorted unique symbol|delistedDate|exchange over CLEAN bucket",
            "excluded_examples": excl[:40],
            "uncertain_examples": unc[:40],
            "clean_equity_symbols": sorted({r["symbol"] for r in clean}),
            "uncertain_symbols": sorted({r["symbol"] for r in unc}),
            "caveats": caveats,
        },
        root=root,
    )

    survivors = sorted({f.stem for f in (root / "cache" / "prices").glob("*.parquet")})
    manifest_path = outdir / "replay_universe_manifest_clean_equities.json"
    if manifest_path.exists() and not args.overwrite_manifest:
        print(
            f"\n{manifest_path.name} already exists — left untouched "
            "(pass --overwrite-manifest to regenerate). The stored manifest is "
            "authoritative for the recorded 2022-2025 results."
        )
    else:
        write_replay_json(
            manifest_path,
            {
                "kind": "REPLAY_UNIVERSE_MANIFEST_CLEAN_EQUITIES",
                "version": "REPLAY_UNIVERSE_V0_CLEAN_EQUITY",
                "research_only": True,
                "not_backtest_evidence": True,
                "note": NOTE,
                "generated_at": now,
                "status": "VALIDATED__PRICE_BACKFILL_NOT_STARTED",
                "harvest_window": {"start": HARVEST_START, "end": HARVEST_END},
                "clean_equity_count": len(clean),
                "hash_sha256": clean_hash,
                "hash_basis": "sorted unique symbol|delistedDate|exchange over CLEAN bucket",
                "by_year": dict(sorted(byyear.items())),
                "by_exchange": dict(byexch.most_common()),
                "survivor_pool_count": len(survivors),
                "clean_equity_symbols": sorted({r["symbol"] for r in clean}),
                "caveats": caveats,
            },
            root=root,
        )
    print(f"\nclean-equity hash: {clean_hash}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="stage", required=True)

    h = sub.add_parser("harvest", help="page /delisted-companies (PROVIDER CALLS)")
    add_safety_args(h, fetches=True)
    h.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES)
    h.set_defaults(func=harvest)

    f = sub.add_parser("filter", help="local ETP/fund/derivative filter (zero API)")
    add_safety_args(f)
    f.add_argument(
        "--overwrite-manifest",
        action="store_true",
        help="Regenerate the clean-equity manifest even if one is stored.",
    )
    f.set_defaults(func=filter_equities)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run_cli(args.func, args)


if __name__ == "__main__":
    sys.exit(main())
