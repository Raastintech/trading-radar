#!/usr/bin/env python3
"""
scripts/check_fund.py — operator-invoked single-fund 13F holdings report.

Accepts either a fund name (full-text search against EDGAR) or a CIK,
then prints the most recent 13F-HR holdings for that filer along with a
Q-over-Q diff against the prior quarter: NEW positions, INCREASED
positions, DECREASED positions, and FULLY EXITED positions.

This is a complement to check_13f.py:
  - check_13f.py: ticker-centric — "who is buying/selling AAPL?"
  - check_fund.py: fund-centric  — "what did Situational Awareness LP buy?"

Cache: results are fetched fresh from SEC EDGAR via edgartools each run.
A single fund's filing is small (typically 10–500 rows per quarter) and
SEC requests are throttled internally by edgartools, so caching isn't
necessary for ad-hoc operator use.

Usage:
  SNIPER_ENV_PATH=/home/gem/secure/trading.env \\
    .venv/bin/python scripts/check_fund.py "Situational Awareness LP"
  SNIPER_ENV_PATH=/home/gem/secure/trading.env \\
    .venv/bin/python scripts/check_fund.py 2045724
  SNIPER_ENV_PATH=/home/gem/secure/trading.env \\
    .venv/bin/python scripts/check_fund.py "Pershing Square" --top 10
  SNIPER_ENV_PATH=/home/gem/secure/trading.env \\
    .venv/bin/python scripts/check_fund.py "Citadel Advisors" --raw
  SNIPER_ENV_PATH=/home/gem/secure/trading.env \\
    .venv/bin/python scripts/check_fund.py 2045724 --json

Exit codes:
  0   report printed
  1   fund / CIK not found, or no 13F-HR filings on file
  2   environment / config error
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

# Standard env-load pattern used by every credential-requiring tool.
_env_path = os.environ.get("SNIPER_ENV_PATH", "")
if _env_path:
    try:
        from dotenv import load_dotenv as _load
        _load(Path(_env_path), override=True)
    except ImportError:
        pass

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_SEC_IDENTITY = "hedayat.raastin@gmail.com"


def _resolve_fund(query: str) -> List[Tuple[str, str]]:
    """Return list of (CIK_padded, display_name) candidates matching `query`.
    If `query` is a pure integer, treats it as a CIK directly.  Otherwise
    runs an EDGAR full-text search restricted to 13F-HR filings."""
    q = query.strip()
    if q.isdigit():
        # Direct CIK — return as-is (10-digit zero-padded)
        return [(q.zfill(10), f"CIK {q.zfill(10)}")]

    import requests
    headers = {"User-Agent": _SEC_IDENTITY}
    url = "https://efts.sec.gov/LATEST/search-index"
    params = {"q": f'"{q}"', "forms": "13F-HR"}
    try:
        r = requests.get(url, params=params, headers=headers, timeout=15)
        r.raise_for_status()
        data = r.json()
    except Exception as exc:
        print(f"EDGAR full-text search failed: {exc}", file=sys.stderr)
        return []

    seen: Dict[str, str] = {}  # CIK → display name
    for hit in (data.get("hits", {}).get("hits") or []):
        src = hit.get("_source", {})
        ciks = src.get("ciks") or []
        names = src.get("display_names") or []
        for cik, name in zip(ciks, names):
            cik_pad = str(cik).zfill(10)
            if cik_pad not in seen:
                seen[cik_pad] = name
    return [(c, n) for c, n in seen.items()]


def _summarize_filing(filing: Any) -> Dict[str, Any]:
    """Pull (period, holdings_df, count, total_value) from a 13F-HR filing.
    Returns {} on any parse error."""
    try:
        period = str(filing.period_of_report or "")[:10]
        obj = filing.obj()
        df = obj.holdings
        if df is None or df.empty:
            return {}
        # Normalize: ensure expected columns present
        for col in ("Issuer", "Ticker", "SharesPrnAmount", "Value"):
            if col not in df.columns:
                df[col] = None
        total_value = int(df["Value"].fillna(0).sum())
        return {
            "period": period,
            "df": df,
            "count": len(df),
            "total_value": total_value,
        }
    except Exception as exc:
        print(f"  ⚠ failed to parse filing: {exc}", file=sys.stderr)
        return {}


def _fmt_money(v: float) -> str:
    v = float(v or 0)
    if abs(v) >= 1e9:
        return f"${v/1e9:>6.2f}B"
    if abs(v) >= 1e6:
        return f"${v/1e6:>6.1f}M"
    if abs(v) >= 1e3:
        return f"${v/1e3:>6.1f}K"
    return f"${v:>7.0f}"


def _fmt_shares(n: float) -> str:
    n = float(n or 0)
    if abs(n) >= 1e6:
        return f"{n/1e6:>7.2f}M"
    if abs(n) >= 1e3:
        return f"{n/1e3:>7.1f}K"
    return f"{n:>8.0f}"


def _str_cell(value: Any) -> str:
    """Coerce a pandas cell (possibly NaN/None/int/str) to a clean string."""
    if value is None:
        return ""
    # pandas uses NaN (float) for missing cells; both isnan and != itself work
    try:
        if isinstance(value, float) and value != value:  # NaN check
            return ""
    except Exception:
        pass
    return str(value).strip()


_CORP_SUFFIXES = (
    " INCORPORATED", " INC NEW", " INC", " CORPORATION", " CORP NEW", " CORP",
    " COMPANY", " COMPANIES", " CO NEW", " CO", " LIMITED", " LTD",
    " HOLDINGS", " HLDGS", " HLDG", " GROUP", " GRP",
    " PLC", " P L C", " N V", " NV", " S A", " SA",
    " CLASS A", " CLASS B", " CL A", " CL B", " COM", " ORD",
)


def _norm_issuer(name: str) -> str:
    """Aggressive issuer-name normalization for matching across quarters.
    Strips corporate suffixes and standardizes whitespace/punctuation so
    e.g. 'MASTERCARD INC' and 'MASTERCARD INCORPORATED' collapse to
    'MASTERCARD'."""
    import re
    s = name.upper().strip()
    s = re.sub(r"[.,&/]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    # Strip corporate suffixes iteratively (handles compound endings like
    # "INC NEW", "CORP NEW", "HLDGS INC").
    changed = True
    while changed:
        changed = False
        for suffix in _CORP_SUFFIXES:
            if s.endswith(suffix):
                s = s[: -len(suffix)].strip()
                changed = True
    return s


def _row_keys(row: Any) -> List[str]:
    """All possible match keys for a position, in priority order.
    The caller does cross-quarter matching by registering prior rows
    under every key and looking up current rows the same way; first hit
    wins.  This handles three failure modes:
      1. Ticker missing in one quarter (CIFR Q3 ticker=NaN).
      2. CUSIP differs across share classes (CIFR notes vs common).
      3. Issuer name formatting changes ("MASTERCARD INC" vs
         "MASTERCARD INCORPORATED", "BANK AMER" vs "BANK AMERICA").
    """
    keys: List[str] = []
    ticker = _str_cell(row.get("Ticker"))
    if ticker:
        keys.append(f"T:{ticker.upper()}")
    cusip = _str_cell(row.get("Cusip"))
    if cusip:
        keys.append(f"C:{cusip}")
    issuer = _str_cell(row.get("Issuer"))
    if issuer:
        keys.append(f"I:{_norm_issuer(issuer)}")
    return keys or ["?"]


def _build_diff(current_df: Any, prior_df: Any) -> Dict[str, List[Dict[str, Any]]]:
    """Compare two 13F holdings DataFrames row-by-row.  Returns dict with
    keys: new, increased, decreased, sold_out (each a list of {ticker,
    issuer, current_shares, prior_shares, current_value, prior_value,
    shares_change, pct_change})."""
    def _collect(df: Any) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
        """Build (positions list, key_index → position index) for one
        quarter.  Aggregates same-issuer rows (different share classes).
        Returns ordered list + key index so multi-key lookup is O(1).
        """
        positions: List[Dict[str, Any]] = []
        key_index: Dict[str, int] = {}
        for _, r in df.iterrows():
            keys = _row_keys(r)
            existing_idx = next((key_index[k] for k in keys if k in key_index), None)
            ticker = _str_cell(r.get("Ticker"))
            issuer = _str_cell(r.get("Issuer"))
            shares = int(float(r.get("SharesPrnAmount") or 0))
            value = int(float(r.get("Value") or 0))
            if existing_idx is not None:
                pos = positions[existing_idx]
                pos["shares"] += shares
                pos["value"] += value
                # Prefer non-empty ticker / longer issuer name when aggregating
                if ticker and not pos["ticker"]:
                    pos["ticker"] = ticker
                if issuer and len(issuer) > len(pos["issuer"]):
                    pos["issuer"] = issuer
                # Add any new keys for this row to the index
                for k in keys:
                    if k not in key_index:
                        key_index[k] = existing_idx
            else:
                idx = len(positions)
                positions.append({
                    "ticker": ticker,
                    "issuer": issuer,
                    "shares": shares,
                    "value": value,
                    "keys": keys,
                })
                for k in keys:
                    key_index[k] = idx
        return positions, key_index

    cur_list, cur_index = _collect(current_df)
    prior_list, prior_index = _collect(prior_df)

    new_pos: List[Dict[str, Any]] = []
    inc_pos: List[Dict[str, Any]] = []
    dec_pos: List[Dict[str, Any]] = []
    sold_pos: List[Dict[str, Any]] = []
    matched_prior_idx: set = set()

    for cur in cur_list:
        prior_idx = next((prior_index[k] for k in cur["keys"] if k in prior_index), None)
        if prior_idx is None:
            new_pos.append({**cur, "shares_change": cur["shares"], "pct_change": None})
            continue
        matched_prior_idx.add(prior_idx)
        prior = prior_list[prior_idx]
        delta = cur["shares"] - prior["shares"]
        pct = (delta / prior["shares"] * 100.0) if prior["shares"] else None
        if delta > 0:
            inc_pos.append({**cur, "prior_shares": prior["shares"],
                            "shares_change": delta, "pct_change": pct})
        elif delta < 0:
            dec_pos.append({**cur, "prior_shares": prior["shares"],
                            "shares_change": delta, "pct_change": pct})
        # else (delta==0) → unchanged, omitted from diff

    for idx, prior in enumerate(prior_list):
        if idx in matched_prior_idx:
            continue
        sold_pos.append({**prior, "shares_change": -prior["shares"],
                         "pct_change": -100.0})

    # Sort: new+increased by current value desc; decreased+sold_out by prior value desc
    new_pos.sort(key=lambda x: -x["value"])
    inc_pos.sort(key=lambda x: -x["value"])
    dec_pos.sort(key=lambda x: -x["value"])
    sold_pos.sort(key=lambda x: -x["value"])
    return {"new": new_pos, "increased": inc_pos, "decreased": dec_pos, "sold_out": sold_pos}


def _print_diff_section(title: str, rows: List[Dict[str, Any]], symbol: str, top: Optional[int]) -> None:
    if not rows:
        return
    shown = rows if top is None else rows[:top]
    print(f"\n  {title} ({len(rows)} {'shown' if top is None or top >= len(rows) else f'top {len(shown)} of {len(rows)}'}):")
    for r in shown:
        ticker = (r.get("ticker") or "—")[:6]
        issuer = (r.get("issuer") or "")[:30]
        if r.get("prior_shares") is not None:
            shares_str = f"{_fmt_shares(r['prior_shares'])} → {_fmt_shares(r['shares'])}"
        else:
            shares_str = f"{'':>16}{_fmt_shares(r['shares'])}"
        pct = r.get("pct_change")
        pct_str = f"{pct:+7.1f}%" if pct is not None else "    NEW "
        val_str = _fmt_money(r["value"])
        print(f"    {symbol} {ticker:6}  {issuer:30}  {shares_str}  {pct_str}  {val_str}")


def _print_current_only(df: Any, top: Optional[int]) -> None:
    sorted_df = df.sort_values("Value", ascending=False)
    if top is not None:
        sorted_df = sorted_df.head(top)
    print(f"\n  {'TICKER':6}  {'ISSUER':30}  {'SHARES':>10}  {'VALUE':>10}")
    for _, r in sorted_df.iterrows():
        ticker = (_str_cell(r.get("Ticker")) or "—")[:6]
        issuer = _str_cell(r.get("Issuer"))[:30]
        shares = _fmt_shares(float(r.get("SharesPrnAmount") or 0))
        val = _fmt_money(float(r.get("Value") or 0))
        print(f"  {ticker:6}  {issuer:30}  {shares}  {val}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Print recent 13F holdings for a single fund / institution.",
    )
    parser.add_argument(
        "fund",
        help='Fund name or CIK.  Examples: "Situational Awareness LP", '
             '"Pershing Square", 2045724',
    )
    parser.add_argument(
        "--top", type=int, default=None,
        help="Limit each section to top N positions by value (default: all)",
    )
    parser.add_argument(
        "--raw", action="store_true",
        help="Show current-quarter holdings only — skip Q-over-Q diff.",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Print machine-readable JSON instead of the text report.",
    )
    parser.add_argument(
        "--list", action="store_true",
        help="Only list candidate funds matching the search string and exit.  "
             "Useful when the name is ambiguous between multiple filers.",
    )
    args = parser.parse_args(argv)

    try:
        from edgar import set_identity, Company
        set_identity(_SEC_IDENTITY)
    except ImportError:
        print("edgartools not installed: pip install edgartools", file=sys.stderr)
        return 2

    candidates = _resolve_fund(args.fund)
    if not candidates:
        print(f"no 13F-HR filers matched: {args.fund!r}", file=sys.stderr)
        return 1

    if args.list or len(candidates) > 1 and not args.fund.strip().isdigit():
        if args.list:
            print(f"matches for {args.fund!r}:")
        else:
            print(f"⚠  multiple 13F filers matched {args.fund!r} — pick one and re-run with the CIK:")
        for cik, name in candidates:
            print(f"  CIK {cik}  {name}")
        return 0 if args.list else 1

    cik, display_name = candidates[0]
    try:
        c = Company(int(cik))
    except Exception as exc:
        print(f"failed to load company at CIK {cik}: {exc}", file=sys.stderr)
        return 1

    f13 = c.get_filings(form="13F-HR")
    if f13 is None or len(f13) == 0:
        print(f"{c.name} (CIK {cik}) has no 13F-HR filings on file", file=sys.stderr)
        return 1

    n_to_pull = 1 if args.raw else min(2, len(f13))
    latest = f13.latest(n_to_pull)
    current = _summarize_filing(latest.get_filing_at(0))
    prior = _summarize_filing(latest.get_filing_at(1)) if (not args.raw and n_to_pull >= 2) else {}

    if not current:
        print("failed to parse current filing", file=sys.stderr)
        return 1

    # ── JSON output ────────────────────────────────────────────────────────
    if args.json:
        df = current["df"]
        records: List[Dict[str, Any]] = []
        for _, r in df.iterrows():
            records.append({
                "ticker": _str_cell(r.get("Ticker")),
                "issuer": _str_cell(r.get("Issuer")),
                "cusip":  _str_cell(r.get("Cusip")),
                "shares": int(float(r.get("SharesPrnAmount") or 0)),
                "value":  int(float(r.get("Value") or 0)),
            })
        payload: Dict[str, Any] = {
            "fund_name": c.name,
            "cik": cik,
            "period": current["period"],
            "total_holdings": current["count"],
            "total_value": current["total_value"],
            "holdings": records,
        }
        if prior:
            diff = _build_diff(current["df"], prior["df"])
            payload["prior_period"] = prior["period"]
            payload["prior_total_value"] = prior["total_value"]
            payload["diff"] = diff
        print(json.dumps(payload, indent=2, default=str))
        return 0

    # ── Text output ────────────────────────────────────────────────────────
    bar = "═" * 72
    print()
    print(bar)
    print(f"  {c.name}".ljust(50) + f"CIK {cik}")
    if prior:
        delta_v = current["total_value"] - prior["total_value"]
        pct_v = (delta_v / prior["total_value"] * 100.0) if prior["total_value"] else 0
        print(
            f"  Period: {current['period']}    "
            f"Prior: {prior['period']}    "
            f"AUM Δ: {pct_v:+.1f}%"
        )
    else:
        print(f"  Period: {current['period']}    (no prior-quarter comparison)")
    print(bar)
    print(f"  total holdings: {current['count']:>4}" + (f"     prior: {prior['count']:>4}" if prior else ""))
    print(f"  reported value: {_fmt_money(current['total_value'])}" +
          (f"     prior: {_fmt_money(prior['total_value'])}" if prior else ""))

    if not prior:
        # No diff — just dump current holdings sorted by value
        print(f"\n  CURRENT HOLDINGS:")
        _print_current_only(current["df"], args.top)
        return 0

    diff = _build_diff(current["df"], prior["df"])
    _print_diff_section("NEW POSITIONS",      diff["new"],       "+", args.top)
    _print_diff_section("INCREASED POSITIONS", diff["increased"], "▲", args.top)
    _print_diff_section("DECREASED POSITIONS", diff["decreased"], "▼", args.top)
    _print_diff_section("SOLD OUT",            diff["sold_out"],  "✗", args.top)

    # Tail: anything unchanged?
    held = current["count"] - len(diff["new"]) - len(diff["increased"]) - len(diff["decreased"])
    if held > 0:
        print(f"\n  HELD UNCHANGED: {held} positions (omitted)")

    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
