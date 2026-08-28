#!/usr/bin/env python3
"""research/topic_shock_review.py — Topic Shock Review CLI.

RESEARCH-ONLY / READ-ONLY.  Converts the already-generated
``cache/research/topic_shock_detector_latest.json`` into a human-friendly
terminal summary.  This module is a pure reader/formatter: it computes no
new topic-shock scoring, changes no label, writes no cache/data/log
artifact, and never touches the scanner, gates, HC/EO rules, Alpha Focus
rules, program verdicts, or routing.  The only thing it adds beyond the
detector's own JSON is presentation — including a set of "quality warning"
heuristics (broad known category, generic evidence phrase, single-source
cluster, mega-cap ticker-soup) that flag weak topic naming IN THE REVIEW
TEXT ONLY.  They never change ``operator_label``, scores, or the JSON file.

Usage:
    python -m research.topic_shock_review
    python -m research.topic_shock_review --only research-now
    python -m research.topic_shock_review --only watch-reset
    python -m research.topic_shock_review --only suppressed
    python -m research.topic_shock_review --format compact
    python -m research.topic_shock_review --format full
    python -m research.topic_shock_review --format markdown
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research import alpha_heat_radar as ahr  # noqa: E402  (TOPIC_RULES only, for quality checks)
from research import topic_shock_detector as tsd  # noqa: E402  (label constants + default path only)

DEFAULT_PATH = tsd.REPO_ROOT / tsd.OUT_JSON_REL

DISCLAIMER = (
    "RESEARCH-ONLY review. This is a read-only presentation of the Topic "
    "Shock Detector's own output — it computes no new scores, changes no "
    "label, and is not a trade signal."
)

# ── quality-warning heuristics (display-only; never fed back into scoring) ──
# Learned from real cache snapshots: earnings-season chatter, mega-cap
# ticker-name co-mentions, and index/ETF cashtag soup dominate raw text and
# will otherwise look like "topics" to an operator skimming quickly.
GENERIC_MARKER_TERMS = (
    "wall street", "earnings call", "earnings result", "full earnings",
    "2026 earnings", "nvidia earnings", "price action", "price trend",
    "spy qqq", "investing microcap", "investing smallcap", "read note",
)
MEGA_CAP_TICKERS = frozenset({"AAPL", "MSFT", "AMZN", "GOOGL", "GOOG", "META", "NVDA", "TSLA", "AVGO"})
MEGA_CAP_TOKENS = frozenset(t.lower() for t in MEGA_CAP_TICKERS)

RISK_LABELS = (tsd.LABEL_REDFLAG_NOISE, tsd.LABEL_EXHAUSTION_RISK,
              tsd.LABEL_OLD_RECHURN, tsd.LABEL_MAPPING_WEAK)
KNOWN_LABELS = frozenset({tsd.LABEL_RESEARCH_NOW, tsd.LABEL_WATCH_FOR_RESET,
                         tsd.LABEL_CONFIRMATION_ONLY, *RISK_LABELS})

SECTION_TITLES = {
    "TOPIC SHOCK REVIEW", "RESEARCH NOW", "WATCH FOR RESET",
    "CONFIRMATION ONLY (known, not fresh)", "RISK / NOISE",
    "SUPPRESSED TEMPLATES", "QUALITY WARNINGS", "OPERATOR SUMMARY",
}


# ── loading (read-only) ──────────────────────────────────────────────────
def load_report(path: Path) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Returns (data, error_message).  ``data`` is None on any failure —
    missing file, unreadable file, or invalid JSON — and never raises."""
    if not path.exists():
        return None, (f"No Topic Shock Detector artifact found at {path}.\n"
                      f"Run: python -m research.topic_shock_detector")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return None, f"Could not parse {path}: {exc}"
    return data, None


# ── small display helpers (presentation only — no scoring) ─────────────────
def _by_label(data: Dict[str, Any], label: str) -> List[Dict[str, Any]]:
    return [a for a in data.get("alerts") or [] if a.get("operator_label") == label]


def _top_tickers(alert: Dict[str, Any], n: int = 3) -> List[Dict[str, Any]]:
    mapped = sorted(alert.get("mapped_tickers") or [], key=lambda t: -t.get("confidence", 0.0))
    return mapped[:n]


def _display_tickers(alert: Dict[str, Any], n: int = 3) -> List[str]:
    """The cited (best alpha-fit) ticker first, then top-confidence tickers,
    deduplicated — so the "why blocked/extended" reason text (which always
    names the best ticker) never cites a ticker absent from the displayed
    list."""
    best = _best_qualifying_ticker(alert)
    out: List[str] = [best["ticker"]] if best else []
    for t in _top_tickers(alert, n=n):
        if t["ticker"] not in out:
            out.append(t["ticker"])
        if len(out) >= n:
            break
    return out or ["n/a"]


def _display_ticker(alert: Dict[str, Any]) -> str:
    return _display_tickers(alert, n=1)[0]


def _best_qualifying_ticker(alert: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Mirrors the tie-break topic_shock_detector.assign_topic_label already
    used to pick which ticker's story is cited in ``reason`` — recomputed
    here purely for display, never fed back into any decision."""
    overlays = alert.get("alpha_fit_overlays") or {}
    if not overlays:
        return None
    conf_by_ticker = {t["ticker"]: t.get("confidence", 0.0) for t in alert.get("mapped_tickers") or []}
    best_ticker = max(overlays, key=lambda t: (overlays[t]["alpha_fit"]["score"], conf_by_ticker.get(t, 0.0)))
    return {"ticker": best_ticker, "confidence": conf_by_ticker.get(best_ticker), **overlays[best_ticker]}


def _is_ticker_soup(phrase: str) -> bool:
    toks = phrase.split()
    return bool(toks) and all(t in MEGA_CAP_TOKENS for t in toks)


def quality_flags(alert: Dict[str, Any]) -> List[str]:
    """Display-only quality warnings.  Never mutates the alert or changes
    ``operator_label`` — purely additive review commentary."""
    flags: List[str] = []
    phrases = [p.lower() for p in (alert.get("evidence_phrases") or [])]
    known_topic = alert.get("known_topic")

    if known_topic:
        flags.append(f'Broad known category ("{known_topic}") — a sector bucket, not a fresh specific shock.')

    if any(marker in p for p in phrases for marker in GENERIC_MARKER_TERMS):
        flags.append("Generic cluster — not a clean topic shock.")

    if any(_is_ticker_soup(p) for p in phrases):
        flags.append("Evidence phrase is just mega-cap ticker names next to each other, not a real theme.")

    if (alert.get("source_diversity") or 0) <= 1:
        flags.append("Single-source cluster — weak corroboration.")

    mapped = alert.get("mapped_tickers") or []
    qualifying = {t["ticker"] for t in mapped if t.get("qualifies")}
    if len(qualifying & MEGA_CAP_TICKERS) >= 2:
        flags.append("Mega-cap chatter co-mention present — dilutes the specific-shock signal.")

    if known_topic:
        basket = set(ahr.TOPIC_RULES.get(known_topic, {}).get("tickers", ()))
        explicit = {t["ticker"] for t in mapped if t.get("qualifies") and t.get("method") != "sector_basket"}
        if explicit and basket and not (explicit & basket):
            flags.append(f'Maps tickers unrelated to "{known_topic}"\'s own basket — likely a coincidental keyword match.')

    # de-dup while preserving order
    seen: set = set()
    out: List[str] = []
    for f in flags:
        if f not in seen:
            seen.add(f)
            out.append(f)
    return out


# ── verdict / suggested action ───────────────────────────────────────────
def compute_verdict(data: Dict[str, Any]) -> str:
    counts = data.get("counts") or {}
    total = counts.get("alerts", 0)
    research_now = counts.get(tsd.LABEL_RESEARCH_NOW, 0)
    watch_reset = counts.get(tsd.LABEL_WATCH_FOR_RESET, 0)

    if research_now == 1:
        return "1 topic requires manual research now."
    if research_now > 1:
        return f"{research_now} topics require manual research now."
    if total == 0:
        return "No topic-shock clusters survived template suppression today."

    alerts = data.get("alerts") or []
    generic_flagged = sum(1 for a in alerts if quality_flags(a))
    if generic_flagged >= max(1, round(total * 0.5)):
        return "Most heat today is recycled or generic."
    if watch_reset >= 1 and watch_reset >= total - watch_reset:
        return "Main action today is wait-for-reset, not chase."
    return "No true topic-shock alpha candidate today."


def suggested_action(data: Dict[str, Any]) -> str:
    counts = data.get("counts") or {}
    total = counts.get("alerts", 0)
    research_now = counts.get(tsd.LABEL_RESEARCH_NOW, 0)
    watch_reset = counts.get(tsd.LABEL_WATCH_FOR_RESET, 0)
    redflag = counts.get(tsd.LABEL_REDFLAG_NOISE, 0)
    exhaustion = counts.get(tsd.LABEL_EXHAUSTION_RISK, 0)

    if research_now >= 1:
        return "Review now"
    if total == 0:
        return "No action today"
    if watch_reset >= 1:
        return "Wait for reset"
    if (redflag + exhaustion) >= max(1, round(total * 0.5)):
        return "Ignore as noise"
    return "No action today"


# ── section builders (plain text; markdown is a post-process, see _to_markdown) ──
def build_header(data: Dict[str, Any], args: argparse.Namespace) -> List[str]:
    corpus = data.get("corpus") or {}
    counts = data.get("counts") or {}
    count_line = ", ".join(f"{k}={v}" for k, v in counts.items() if k != "alerts")
    return [
        "TOPIC SHOCK REVIEW",
        f"as-of: {data.get('asof_date', '?')}   generated: {data.get('generated_at', '?')}",
        f"corpus: {corpus.get('raw_snippets', '?')} raw -> {corpus.get('surviving_snippets', '?')} survived "
        f"-> {corpus.get('clusters_formed', '?')} clusters ({corpus.get('suppressed_snippets', '?')} suppressed as template noise)",
        f"alerts: {count_line or 'none'}  (total {counts.get('alerts', 0)})",
        "",
        data.get("disclaimer") or DISCLAIMER,
    ]


def build_research_now(data: Dict[str, Any], args: argparse.Namespace) -> List[str]:
    alerts = _by_label(data, tsd.LABEL_RESEARCH_NOW)
    lines = ["RESEARCH NOW", "-" * 40]
    if not alerts:
        lines.append("  (none) — no topic cleared the bar for manual research today.")
        return lines
    for a in alerts:
        best = _best_qualifying_ticker(a)
        ticker = best["ticker"] if best else "n/a"
        positives = ", ".join((best or {}).get("alpha_fit", {}).get("positive_reasons") or []) or "n/a"
        lines += [
            f"* {a['topic']}  (best: {ticker})",
            f"    heat={a.get('heat_score', 0):.0f}  novelty={a.get('novelty_score', 0):.0f}  "
            f"source_diversity={a.get('source_diversity', 0)}",
            f"    why it matters: {a.get('thesis', '')}",
            f"    alpha-fit reason: {positives}",
            f"    risk flags: {', '.join(a.get('noise_risk_flags') or []) or 'none'}",
            "    action: review manually — this is today's cleanest candidate.",
        ]
        if args.format == "full":
            all_tickers = ", ".join(f"{t['ticker']}({t['confidence']:.2f})" for t in a.get("mapped_tickers") or [])
            lines.append(f"    all mapped tickers: {all_tickers}")
        lines.append("")
    return lines


def build_watch_for_reset(data: Dict[str, Any], args: argparse.Namespace) -> List[str]:
    alerts = _by_label(data, tsd.LABEL_WATCH_FOR_RESET)
    lines = ["WATCH FOR RESET", "-" * 40]
    if not alerts:
        lines.append("  (none)")
        return lines
    cap = None if args.format == "full" else 8
    shown = alerts if cap is None else alerts[:cap]
    for a in shown:
        n = 1 if args.format == "compact" else 3
        tick_str = ", ".join(_display_tickers(a, n=n))
        if args.format == "compact":
            lines.append(f"* {a['topic']} ({tick_str}) — {a.get('reason', '')}")
            continue
        lines += [
            f"* {a['topic']}  (tickers: {tick_str})",
            f"    why blocked/extended: {a.get('reason', '')}",
            "    becomes researchable if: the topic re-accelerates and the ticker "
            "clears its extension/gate-block.",
            "",
        ]
    if cap is not None and len(alerts) > cap:
        lines.append(f"  ... and {len(alerts) - cap} more (use --format full to see all)")
    return lines


def build_confirmation_only(data: Dict[str, Any], args: argparse.Namespace) -> List[str]:
    alerts = _by_label(data, tsd.LABEL_CONFIRMATION_ONLY)
    lines = ["CONFIRMATION ONLY (known, not fresh)", "-" * 40]
    if not alerts:
        lines.append("  (none)")
        return lines
    for a in alerts:
        lines.append(f"* {a['topic']} ({_display_ticker(a)}) — {a.get('reason', '')}")
    return lines


def build_risk_noise(data: Dict[str, Any], args: argparse.Namespace) -> List[str]:
    alerts = data.get("alerts") or []
    lines = ["RISK / NOISE", "-" * 40]
    any_found = False
    for label in RISK_LABELS:
        group = [a for a in alerts if a.get("operator_label") == label]
        if not group:
            continue
        any_found = True
        lines.append(f"{label.replace('TOPIC_', '').replace('_', ' ').title()} ({len(group)}):")
        for a in group:
            lines.append(f"  - {a['topic']} ({_display_ticker(a)}) — {a.get('reason', '')}")
    # Catch-all so any future/unexpected noise-flavored label (e.g. a literal
    # SOCIAL_NOISE string, should the schema ever grow one) is never silently
    # hidden from the operator.
    other = [a for a in alerts if a.get("operator_label") not in KNOWN_LABELS]
    if other:
        any_found = True
        lines.append("Other noise-flagged items:")
        for a in other:
            lines.append(f"  - {a['topic']} [{a.get('operator_label')}] — {a.get('reason', '')}")
    if not any_found:
        lines.append("  (none) — no red flags, exhaustion, recycled, or weak-mapping topics today.")
    return lines


def build_suppressed(data: Dict[str, Any], args: argparse.Namespace) -> List[str]:
    suppressed = data.get("suppressed_as_template") or []
    lines = ["SUPPRESSED TEMPLATES", "-" * 40]
    if not suppressed:
        lines.append("  (none) — no syndicated/template boilerplate detected today.")
        return lines
    lines.append("Correctly suppressed BEFORE clustering as syndicated/template noise — not dropped silently:")
    for s in suppressed:
        tickers = ", ".join((s.get("tickers") or [])[:6])
        lines.append(f'* {s.get("matched_pattern")}  docs={s.get("document_count")}  '
                     f'phrase="{s.get("matched_phrase")}"  tickers=[{tickers}]')
    return lines


def build_quality_warnings(data: Dict[str, Any], args: argparse.Namespace) -> List[str]:
    alerts = data.get("alerts") or []
    lines = ["QUALITY WARNINGS", "-" * 40]
    any_found = False
    for a in alerts:
        flags = quality_flags(a)
        if not flags:
            continue
        any_found = True
        lines.append(f"* {a['topic']} [{a.get('operator_label')}]:")
        for f in flags:
            lines.append(f"    - {f}")
    if not any_found:
        lines.append("  (none) — no naming/mapping quality concerns detected.")
    return lines


def build_operator_summary(data: Dict[str, Any], args: argparse.Namespace) -> List[str]:
    counts = data.get("counts") or {}
    suppressed = data.get("suppressed_as_template") or []
    suppressed_docs = sum(s.get("document_count", 0) for s in suppressed)
    return [
        "OPERATOR SUMMARY",
        "-" * 40,
        f"Research Now: {counts.get(tsd.LABEL_RESEARCH_NOW, 0)}",
        f"Watch Reset: {counts.get(tsd.LABEL_WATCH_FOR_RESET, 0)}",
        f"Suppressed noise: {len(suppressed)} template categories ({suppressed_docs} documents)",
        f"Suggested next action: {suggested_action(data)}",
    ]


SECTION_BUILDERS = {
    "research-now": build_research_now,
    "watch-reset": build_watch_for_reset,
    "confirmation-only": build_confirmation_only,
    "risk": build_risk_noise,
    "suppressed": build_suppressed,
}
ALL_SECTIONS = ["research-now", "watch-reset", "confirmation-only", "risk", "suppressed"]


def build_report(data: Dict[str, Any], args: argparse.Namespace) -> List[str]:
    if data.get("fallback"):
        return [
            "TOPIC SHOCK REVIEW",
            f"Topic Shock Detector had no data to work with today "
            f"(fallback={data['fallback']}, missing={data.get('missing_artifacts')}).",
            "Nothing to review.",
        ]

    lines = build_header(data, args)
    lines += ["", "VERDICT: " + compute_verdict(data), ""]

    sections = ALL_SECTIONS if args.only == "all" else [args.only]
    for key in sections:
        lines += SECTION_BUILDERS[key](data, args)
        lines.append("")

    if args.only == "all" and args.format != "compact":
        lines += build_quality_warnings(data, args)
        lines.append("")

    lines += build_operator_summary(data, args)
    return lines


def _to_markdown(lines: List[str]) -> List[str]:
    out: List[str] = []
    for line in lines:
        if line in SECTION_TITLES:
            out.append(f"## {line}")
        elif line and set(line) == {"-"} and len(line) >= 10:
            continue  # drop the plain-text underline rule
        elif line.startswith("* "):
            out.append("- " + line[2:])
        elif line.startswith("    - "):
            out.append("  - " + line[len("    - "):])
        else:
            out.append(line)
    return out


# ── CLI ──────────────────────────────────────────────────────────────────
def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Topic Shock Review — human-friendly summary of the Topic "
                    "Shock Detector artifact (read-only, no writes).")
    ap.add_argument("--path", type=Path, default=None, help=argparse.SUPPRESS)
    ap.add_argument("--only", choices=["all", *ALL_SECTIONS], default="all",
                    help="show only one section (plus header/verdict/summary)")
    ap.add_argument("--format", choices=["plain", "compact", "full", "markdown"], default="plain")
    return ap.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    path = args.path or DEFAULT_PATH
    data, err = load_report(path)
    if data is None:
        print(err)
        return 1

    lines = build_report(data, args)
    if args.format == "markdown":
        lines = _to_markdown(lines)
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
