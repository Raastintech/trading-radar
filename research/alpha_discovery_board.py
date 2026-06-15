#!/usr/bin/env python3
"""
Alpha Discovery Board V2 report.

Research-only output for discretionary/manual long idea generation.
Not sleeve approval, not paper evidence, not auto-tradable.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import os
import sys
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - local runtime convenience only
    load_dotenv = None

if load_dotenv is not None:
    env_path = os.getenv("SNIPER_ENV_PATH", "").strip()
    if env_path:
        load_dotenv(env_path, override=False)

from core.alpha_discovery import (
    build_alpha_discovery_board,
    build_alpha_discovery_overlay,
    load_alpha_discovery_board,
    prewarm_alpha_discovery_enrichment,
    save_alpha_discovery_board,
    save_alpha_discovery_overlay,
)


def _fmt_mcap(value: Any) -> str:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "N/A"
    if v >= 1_000_000_000:
        return f"${v/1_000_000_000:.1f}B"
    return f"${v/1_000_000:.0f}M"


def _render_bucket(items: List[Dict[str, Any]], bucket: str, *, use_overlay: bool = False) -> List[str]:
    out = [f"{bucket}"]
    bucket_key = "overlay_bucket" if use_overlay else "bucket"
    status_key = "overlay_status" if use_overlay else None
    reason_key = "overlay_reason" if use_overlay else None
    rows = [row for row in items if row.get(bucket_key) == bucket]
    if not rows:
        out.append("  none")
        return out
    for row in rows:
        track = str(row.get("track") or "Unknown")
        status = str(row.get(status_key) or "").strip() if status_key else ""
        status_txt = f"  {status}" if status else ""
        out.append(
            "  "
            f"{row['ticker']:<5} "
            f"{track[:22]:<22} "
            f"score {row['alpha_score']:>5.1f}  "
            f"tier {row['data_tier']}  "
            f"{str(row.get('validator_state') or row.get('action_label') or ('buyable now' if row['actionable_now'] else 'watch only')).lower()}"
            f"{status_txt}"
        )
        out.append(
            "    "
            f"why: {row['why_now']} | validator: {row.get('validator_reason') or row['main_risk']}"
        )
        out.append(
            "    "
            f"fit: {row['sleeve_resemblance']} | "
            f"5d {row['return_5d_pct']:+.1f}% | 20d {row['return_20d_pct']:+.1f}% | "
            f"vol {row['volume_ratio_5d']:.1f}x | state {row.get('validator_state','—')} | mcap {_fmt_mcap(row['market_cap'])}"
        )
        flags = row.get("validator_flags") or []
        if flags:
            out.append("    " f"flags: {', '.join(flags[:3])}")
        if reason_key and row.get(reason_key):
            out.append("    " f"overlay: {row[reason_key]}")
    return out


def _render_text(board: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append("ALPHA DISCOVERY BOARD")
    lines.append("Early Opportunity / Buyable Pullback / Sponsor Confirmation")
    lines.append("research-only; not sleeve approval; not paper evidence; not auto-tradable")
    lines.append("")
    if board.get("error"):
        lines.append(f"ERROR: {board['error']}")
        return "\n".join(lines)

    cov = board.get("coverage") or {}
    enrichment = board.get("enrichment_cache") or {}
    counts = board.get("bucket_counts") or {}
    tiers = board.get("tier_counts") or {}
    sectors = board.get("dominant_sectors") or []
    mode = str(board.get("mode") or "nightly")
    items = board.get("items") or []
    if mode == "premarket_overlay":
        lines.append(
            f"overlay source board mtime={board.get('source_board_mtime', '—')} "
            f"quotes={len(items)} surfaced names"
        )
    else:
        lines.append(
            f"coverage seed={cov.get('seed_rows', 0)} filtered={cov.get('filtered_rows', 0)} "
            f"profile={cov.get('profile_rows', 0)} fundamentals={cov.get('fundamental_rows', 0)} "
            f"13f={cov.get('thirteen_f_rows', 0)} tradier={cov.get('tradier_rows', 0)}"
        )
        if enrichment:
            lines.append(
                f"enrichment prewarm band={enrichment.get('candidate_band', 0)} "
                f"profile={enrichment.get('profile_rows', 0)}/{enrichment.get('profile_target', 0)} "
                f"fundamentals={enrichment.get('fundamental_rows', 0)}/{enrichment.get('fundamentals_target', 0)} "
                f"built_at={enrichment.get('built_at', '—')}"
            )
    tracks = board.get("track_counts") or {}
    lines.append(
        f"tracks liquid_reset={tracks.get('Liquid Leadership Reset', 0)} "
        f"emerging={tracks.get('Emerging Opportunity', 0)}"
    )
    lines.append(
        f"buckets early={counts.get('Early Discovery', 0)} "
        f"buyable={counts.get('Buyable Pullback', counts.get('Buyable Now', 0))} "
        f"pullback={counts.get('Pullback Watch', 0)} "
        f"confirm={counts.get('Sponsor Confirmation', 0)} "
        f"crowded={counts.get('Too Late / Crowded', 0)}"
    )
    lines.append(
        f"tiers A={tiers.get('A', 0)} B={tiers.get('B', 0)} C={tiers.get('C', 0)}"
    )
    if sectors:
        lines.append(f"dominant sectors: {', '.join(sectors[:3])}")
    lines.append(f"mode: {mode}  built_at: {board.get('built_at', '—')}")
    lines.append("")

    use_overlay = mode == "premarket_overlay"
    buckets = (
        "Buyable Now",
        "Pullback Watch",
        "Early Discovery",
        "Too Late / Crowded",
    ) if use_overlay else (
        "Buyable Pullback",
        "Sponsor Confirmation",
        "Early Discovery",
        "Too Late / Crowded",
    )
    for bucket in buckets:
        lines.extend(_render_bucket(items, bucket, use_overlay=use_overlay))
        lines.append("")
    if board.get("diagnostics", {}).get("calibration_change"):
        lines.append(f"note: {board['diagnostics']['calibration_change']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Alpha Discovery Board V2")
    parser.add_argument("--mode", choices=("nightly", "premarket"), default="nightly")
    parser.add_argument("--limit", type=int, default=20, help="maximum surfaced names")
    parser.add_argument("--profile-limit", type=int, default=120)
    parser.add_argument("--fundamentals-limit", type=int, default=80)
    parser.add_argument("--overlay-limit", type=int, default=25)
    parser.add_argument("--skip-fmp", action="store_true")
    parser.add_argument("--skip-13f", action="store_true")
    parser.add_argument("--skip-tradier", action="store_true")
    parser.add_argument("--skip-prewarm", action="store_true")
    parser.add_argument("--prewarm-band", type=int, default=320)
    parser.add_argument("--prewarm-profile-limit", type=int, default=240)
    parser.add_argument("--prewarm-fundamentals-limit", type=int, default=160)
    parser.add_argument("--snapshot-path", type=Path, default=None)
    parser.add_argument("--json", action="store_true", help="print json instead of text")
    args = parser.parse_args()

    if args.mode == "nightly":
        if not args.skip_fmp and not args.skip_prewarm:
            prewarm_alpha_discovery_enrichment(
                snapshot_path=args.snapshot_path,
                seed_limit=args.prewarm_band,
                profile_limit=args.prewarm_profile_limit,
                fundamentals_limit=args.prewarm_fundamentals_limit,
            )
        board = build_alpha_discovery_board(
            limit=args.limit,
            profile_limit=args.profile_limit,
            fundamentals_limit=args.fundamentals_limit,
            overlay_limit=args.overlay_limit,
            use_fmp=not args.skip_fmp,
            use_13f=not args.skip_13f,
            use_tradier=not args.skip_tradier,
            snapshot_path=args.snapshot_path,
        )
        paths = save_alpha_discovery_board(board)
        text = _render_text(board)
    else:
        nightly = load_alpha_discovery_board()
        board = build_alpha_discovery_overlay(board=nightly)
        paths = save_alpha_discovery_overlay(board)
        text = _render_text(board)
    Path(paths["text"]).write_text(text, encoding="utf-8")

    if args.json:
        import json
        print(json.dumps(board, indent=2))
    else:
        print(text, end="")
        print(f"saved json: {paths['json']}")
        print(f"saved text: {paths['text']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
