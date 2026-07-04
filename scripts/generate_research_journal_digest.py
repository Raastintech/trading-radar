#!/usr/bin/env python3
"""Generate one Daily Research Digest journal note (Phase 6 format).

Reads the latest cached research artifacts and appends exactly one summary
note to data/research/journal.jsonl so the operator does not need to read
every report manually.

Doctrine:
  - RESEARCH_ONLY.  No trade language; the note carries the standard
    research-only footer.
  - CACHE-ONLY.  Never calls providers, never fetches live data, never
    imports broker/execution modules.
  - APPEND-ONLY.  The only write path is data/research/journal.jsonl, and
    only when --append is passed explicitly.  Default behavior is dry-run.
  - IDEMPOTENT.  A deterministic digest_key prevents duplicate daily spam;
    --force overrides.

Usage:
  GEM_TRADER_SKIP_DOTENV=true .venv/bin/python \
      scripts/generate_research_journal_digest.py --dry-run
  GEM_TRADER_SKIP_DOTENV=true .venv/bin/python \
      scripts/generate_research_journal_digest.py --append
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dashboards.research_command_center.data_adapter import ArtifactStore  # noqa: E402
from dashboards.research_command_center.journal_digest import (  # noqa: E402
    append_digest_entry,
    build_digest_entry,
    find_existing_digest,
)

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Append one daily research digest note to the Phase 6 "
                    "journal (research-only; default is dry-run).")
    p.add_argument("--dry-run", action="store_true",
                   help="print the entry and note body; write nothing")
    p.add_argument("--append", action="store_true",
                   help="append the entry to data/research/journal.jsonl")
    p.add_argument("--force", action="store_true",
                   help="append even if the same digest_key already exists")
    p.add_argument("--date", default=None, metavar="YYYY-MM-DD",
                   help="override the digest date (testing)")
    p.add_argument("--top", type=int, default=10, metavar="N",
                   help="number of top review candidates (default 10)")
    p.add_argument("--root", default=None, help=argparse.SUPPRESS)
    args = p.parse_args(argv)
    if args.date and not _DATE_RE.match(args.date):
        p.error("--date must be YYYY-MM-DD")
    return args


def main(argv=None) -> int:
    args = parse_args(argv)
    store = ArtifactStore(root=Path(args.root)) if args.root else ArtifactStore()

    entry = build_digest_entry(store, date_override=args.date, top_n=args.top)

    if not args.append:
        # Default (and --dry-run): print, write nothing.
        print(json.dumps(entry, ensure_ascii=False, indent=2))
        print("\n--- note body ---\n")
        print(entry["note"])
        print("\n[dry-run] nothing written.")
        return 0

    existing = find_existing_digest(store, entry["digest_key"])
    if existing is not None and not args.force:
        print(f"duplicate digest_key {entry['digest_key']} already in "
              f"{store.journal_jsonl} (entry {existing.get('id')}) — "
              "skipping append (use --force to override).")
        return 0

    append_digest_entry(entry, store)
    print(f"appended digest {entry['digest_key']} "
          f"(status {entry['status']}) to {store.journal_jsonl}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
