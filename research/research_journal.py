"""
research/research_journal.py — Phase 8A ticker research journal.

Durable, append-only JSONL store of *manual* research conclusions: when the
trader looks at a ticker and decides "Watch reclaim", "Bullish but extended",
"Avoid chase", that note should survive across sessions and be visible in
Mode 2 next time the ticker is selected.

Storage:
  data/state/research_notes.jsonl
  one JSON object per line, append-only

Each note:
  note_id            "rn_<8 hex>"
  timestamp          ISO 8601 UTC
  ticker             upper case
  source             "manual" | "system"
  conclusion         free text — the call ("watch reclaim above 102")
  status             bullish | bearish | neutral | watch | avoid
  next_action        free text
  key_levels         optional dict {support: float, resistance: float, ...}
  reason             optional free text
  review_date        optional ISO date — when to revisit
  tags               optional list[str]
  reviewed_at        ISO 8601 UTC, set by mark-reviewed
  linked             optional dict:
                       lens_path             cache path of latest Stock Lens
                       alpha_state           "Alpha tier A/B/C" or "not on board"
                       market_forecast_anchor anchor_date of latest forecast

CLI:
  add --ticker MSFT --conclusion "Watch Reclaim" --next-action "..."
  list [--ticker MSFT] [--limit N]
  latest --ticker MSFT
  due [--as-of YYYY-MM-DD]
  mark-reviewed NOTE_ID [--note "comment"]

Guardrails (Phase 8A):
  - research-only; nothing here gates trades, paper evidence, or governance
  - dashboard reads via load_notes / latest_note / due_notes — never
    invokes a provider
  - all links to Lens / Alpha / Market Forecast are best-effort
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
from datetime import datetime, timezone, date
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

# allow `python research/research_journal.py …` to import core.* when run
# directly; matches the pattern used by other research/ scripts
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

logger = logging.getLogger("research_journal")

STATE_DIR = _ROOT / "data" / "state"
NOTES_PATH = STATE_DIR / "research_notes.jsonl"

# Cache-only paths used by the linkage helpers.  Computed directly from
# _ROOT so the CLI works without provider creds (core.config would force
# Alpaca/FMP env vars at import time, which we don't need here).
_CACHE_RESEARCH_DIR = _ROOT / "cache" / "research"

VALID_STATUS = {"bullish", "bearish", "neutral", "watch", "avoid"}


# ── storage helpers ─────────────────────────────────────────────────────────


def _ensure_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


def _stable_id(ticker: str, ts: str, conclusion: str) -> str:
    h = hashlib.sha1(f"{ticker}|{ts}|{conclusion}".encode("utf-8")).hexdigest()[:8]
    return f"rn_{h}"


def load_notes(*, path: Path = NOTES_PATH) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    out: List[Dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    out.append(json.loads(raw))
                except json.JSONDecodeError:
                    continue
    except OSError as exc:
        logger.warning("failed to read notes at %s: %s", path, exc)
    return out


def _append_note(note: Dict[str, Any], *, path: Path = NOTES_PATH) -> None:
    _ensure_dir(path)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(note, ensure_ascii=False) + "\n")


def _rewrite_notes(notes: Iterable[Dict[str, Any]], *, path: Path = NOTES_PATH) -> None:
    _ensure_dir(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for n in notes:
            fh.write(json.dumps(n, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


# ── linkage to other artifacts (best-effort, cache-only) ────────────────────


def _link_lens_path(ticker: str) -> Optional[str]:
    p = _CACHE_RESEARCH_DIR / f"stock_lens_{ticker.upper()}_latest.json"
    return str(p) if p.exists() else None


def _link_alpha_state(ticker: str) -> Optional[str]:
    """Return a short tag like 'Alpha tier B' / 'not on board' / None on miss."""
    p = _CACHE_RESEARCH_DIR / "alpha_discovery_board_latest.json"
    if not p.exists():
        return None
    try:
        board = json.loads(p.read_text())
    except Exception:
        return None
    rows = board.get("rows") or board.get("candidates") or []
    sym = ticker.upper()
    for r in rows:
        if str(r.get("ticker") or r.get("symbol") or "").upper() == sym:
            tier = r.get("tier") or r.get("alpha_tier")
            return f"Alpha tier {tier}" if tier else "on Alpha board"
    return "not on Alpha board"


def _link_forecast_anchor() -> Optional[str]:
    p = _CACHE_RESEARCH_DIR / "regime_forecast_latest.json"
    if not p.exists():
        return None
    try:
        f = json.loads(p.read_text())
    except Exception:
        return None
    return f.get("anchor_date") or f.get("built_at")


def _build_linked(ticker: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    lp = _link_lens_path(ticker)
    if lp:
        out["lens_path"] = lp
    a = _link_alpha_state(ticker)
    if a:
        out["alpha_state"] = a
    mf = _link_forecast_anchor()
    if mf:
        out["market_forecast_anchor"] = mf
    return out


# ── core operations ─────────────────────────────────────────────────────────


def add_note(
    *,
    ticker: str,
    conclusion: str,
    status: str = "watch",
    next_action: Optional[str] = None,
    reason: Optional[str] = None,
    key_levels: Optional[Dict[str, float]] = None,
    review_date: Optional[str] = None,
    tags: Optional[List[str]] = None,
    source: str = "manual",
    path: Path = NOTES_PATH,
) -> Dict[str, Any]:
    sym = (ticker or "").strip().upper()
    if not sym:
        raise ValueError("ticker is required")
    if not (conclusion or "").strip():
        raise ValueError("conclusion is required")
    st = (status or "watch").strip().lower()
    if st not in VALID_STATUS:
        raise ValueError(f"status must be one of {sorted(VALID_STATUS)}; got {status!r}")
    if review_date:
        # validate ISO date but keep the string form for storage
        date.fromisoformat(review_date)

    ts = _now_iso()
    note: Dict[str, Any] = {
        "note_id": _stable_id(sym, ts, conclusion),
        "timestamp": ts,
        "ticker": sym,
        "source": source,
        "conclusion": conclusion.strip(),
        "status": st,
        "next_action": (next_action or "").strip() or None,
        "key_levels": dict(key_levels) if key_levels else None,
        "reason": (reason or "").strip() or None,
        "review_date": review_date or None,
        "tags": list(tags) if tags else None,
        "reviewed_at": None,
        "linked": _build_linked(sym) or None,
    }
    _append_note(note, path=path)
    return note


def list_notes(
    *,
    ticker: Optional[str] = None,
    limit: Optional[int] = None,
    path: Path = NOTES_PATH,
) -> List[Dict[str, Any]]:
    rows = load_notes(path=path)
    if ticker:
        sym = ticker.upper()
        rows = [r for r in rows if (r.get("ticker") or "").upper() == sym]
    rows.sort(key=lambda r: r.get("timestamp") or "", reverse=True)
    if limit and limit > 0:
        rows = rows[:limit]
    return rows


def latest_note(ticker: str, *, path: Path = NOTES_PATH) -> Optional[Dict[str, Any]]:
    rows = list_notes(ticker=ticker, limit=1, path=path)
    return rows[0] if rows else None


def due_notes(
    *,
    as_of: Optional[date] = None,
    path: Path = NOTES_PATH,
) -> List[Dict[str, Any]]:
    """Notes whose review_date is on/before as_of (default today) and that
    have not yet been marked reviewed."""
    today = as_of or date.today()
    rows = load_notes(path=path)
    out: List[Dict[str, Any]] = []
    for r in rows:
        rd = r.get("review_date")
        if not rd:
            continue
        if r.get("reviewed_at"):
            continue
        try:
            d = date.fromisoformat(str(rd)[:10])
        except Exception:
            continue
        if d <= today:
            out.append(r)
    out.sort(key=lambda r: (r.get("review_date") or "", r.get("ticker") or ""))
    return out


def mark_reviewed(
    note_id: str,
    *,
    comment: Optional[str] = None,
    path: Path = NOTES_PATH,
) -> Optional[Dict[str, Any]]:
    rows = load_notes(path=path)
    found: Optional[Dict[str, Any]] = None
    for r in rows:
        if r.get("note_id") == note_id and not r.get("reviewed_at"):
            r["reviewed_at"] = _now_iso()
            if comment:
                r["review_comment"] = comment.strip()
            found = r
            break
    if found:
        _rewrite_notes(rows, path=path)
    return found


# ── CLI ─────────────────────────────────────────────────────────────────────


def _print_note(n: Dict[str, Any]) -> None:
    rd = n.get("review_date") or "—"
    rev = "reviewed" if n.get("reviewed_at") else "open"
    print(f"{n.get('note_id','?')}  {n.get('timestamp','?')}  "
          f"{n.get('ticker','?'):<6}  {n.get('status','?'):<8}  "
          f"review_due={rd}  [{rev}]")
    print(f"  conclusion : {n.get('conclusion','—')}")
    if n.get("next_action"):
        print(f"  next_action: {n['next_action']}")
    if n.get("reason"):
        print(f"  reason     : {n['reason']}")
    if n.get("key_levels"):
        print(f"  key_levels : {n['key_levels']}")
    if n.get("tags"):
        print(f"  tags       : {', '.join(n['tags'])}")
    if n.get("linked"):
        for k, v in (n["linked"] or {}).items():
            print(f"  link.{k:<22s}: {v}")


def _kv_pairs(values: Optional[List[str]]) -> Optional[Dict[str, float]]:
    if not values:
        return None
    out: Dict[str, float] = {}
    for raw in values:
        if "=" not in raw:
            raise ValueError(f"--key-level must be K=V (got {raw!r})")
        k, v = raw.split("=", 1)
        out[k.strip()] = float(v.strip())
    return out


def _cmd_add(args: argparse.Namespace) -> int:
    note = add_note(
        ticker=args.ticker,
        conclusion=args.conclusion,
        status=args.status,
        next_action=args.next_action,
        reason=args.reason,
        key_levels=_kv_pairs(args.key_level),
        review_date=args.review_date,
        tags=args.tag,
        source=args.source,
    )
    print(f"added {note['note_id']} for {note['ticker']}")
    _print_note(note)
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    rows = list_notes(ticker=args.ticker, limit=args.limit)
    if not rows:
        scope = f"for {args.ticker.upper()}" if args.ticker else ""
        print(f"no notes {scope}".strip())
        return 0
    for r in rows:
        _print_note(r)
        print("")
    return 0


def _cmd_latest(args: argparse.Namespace) -> int:
    note = latest_note(args.ticker)
    if not note:
        print(f"no prior research note for {args.ticker.upper()}")
        return 0
    _print_note(note)
    return 0


def _cmd_due(args: argparse.Namespace) -> int:
    as_of = date.fromisoformat(args.as_of) if args.as_of else None
    rows = due_notes(as_of=as_of)
    if not rows:
        print("no notes due")
        return 0
    print(f"{len(rows)} note(s) due:")
    for r in rows:
        _print_note(r)
        print("")
    return 0


def _cmd_mark_reviewed(args: argparse.Namespace) -> int:
    note = mark_reviewed(args.note_id, comment=args.comment)
    if not note:
        print(f"no open note found with id {args.note_id}")
        return 1
    print(f"marked reviewed {note['note_id']}")
    _print_note(note)
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Ticker research journal (Phase 8A).")
    sub = p.add_subparsers(dest="cmd")
    sub.required = True

    a = sub.add_parser("add", help="Add a research note for a ticker.")
    a.add_argument("--ticker", required=True)
    a.add_argument("--conclusion", required=True,
                   help="Free-text conclusion, e.g. 'Watch reclaim above EMA50'.")
    a.add_argument("--status", default="watch",
                   choices=sorted(VALID_STATUS))
    a.add_argument("--next-action", default=None,
                   help="What to do next, e.g. 'Re-check on close above 102'.")
    a.add_argument("--reason", default=None)
    a.add_argument("--key-level", action="append",
                   help="K=V level, may repeat. e.g. --key-level support=98 --key-level resistance=102")
    a.add_argument("--review-date", default=None,
                   help="ISO date YYYY-MM-DD when to revisit.")
    a.add_argument("--tag", action="append", help="Optional tag, may repeat.")
    a.add_argument("--source", default="manual", choices=["manual", "system"])
    a.set_defaults(func=_cmd_add)

    l = sub.add_parser("list", help="List notes.")
    l.add_argument("--ticker", default=None)
    l.add_argument("--limit", type=int, default=None)
    l.set_defaults(func=_cmd_list)

    lat = sub.add_parser("latest", help="Show the latest note for a ticker.")
    lat.add_argument("--ticker", required=True)
    lat.set_defaults(func=_cmd_latest)

    d = sub.add_parser("due", help="List notes due on/before a date (default today).")
    d.add_argument("--as-of", default=None, help="ISO date YYYY-MM-DD")
    d.set_defaults(func=_cmd_due)

    m = sub.add_parser("mark-reviewed", help="Mark a note as reviewed.")
    m.add_argument("note_id")
    m.add_argument("--comment", default=None)
    m.set_defaults(func=_cmd_mark_reviewed)

    args = p.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
