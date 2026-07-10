#!/usr/bin/env python3
"""Research programs — horizon-aligned routing + program-level validation.

Phase 5 architecture layer.  The production research engine historically
evaluated every candidate with the same short forward evidence regardless
of the hypothesis's intended holding period.  This module fixes the
*measurement* side of that flaw without touching production logic:

  1. ROUTING — every watchlist label maps to exactly one research
     program (Tactical / Swing / Long-Term) with an explicit hypothesis,
     expected holding period, and evaluation horizons.
  2. VALIDATION — per-program and per-label forward evidence is computed
     from the ledger at the horizons the program actually targets, with
     deduped episodes (first appearance per ticker+label), matched
     SPY/QQQ/sector benchmarks, date-clustered bootstrap, and
     pre-registered verdict gates.

Doctrine:
  - RESEARCH-ONLY, READ-ONLY on the engine.  No scanner threshold,
    ranking, score, filter, gate, or promotion rule is changed.  The
    ledger (data/research/research_watchlist_history.jsonl) is never
    mutated.  The only writes are the validation sidecar
    (cache/research/research_program_validation_latest.json) and its
    text log (logs/research_program_validation_latest.txt).
  - PRE-REGISTERED GATES.  Verdict thresholds are constants in this
    module, fixed before the evidence matured.  Do not tune them to the
    data.  INSUFFICIENT_MATURE_EVIDENCE is an acceptable verdict.
  - CRED-FREE.  No providers, no DB, no core.config import.

Verdict ladder (per program and per label):
  VALIDATED_EDGE           — coverage floors met at primary horizons AND
                             positive excess vs QQQ and sector with the
                             cluster-bootstrap CI excluding zero.
  PROMISING_BUT_UNPROVEN   — positive early evidence below the floors.
  NO_EVIDENCE_OF_EDGE      — coverage floors met and excess is zero or
                             negative.
  INSUFFICIENT_MATURE_EVIDENCE — everything else (including horizons the
                             tracker has not matured or does not collect).

Usage:
  GEM_TRADER_SKIP_DOTENV=true .venv/bin/python \
      research/research_programs.py [--dry-run] [--json]
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]

LEDGER_REL = Path("data") / "research" / "research_watchlist_history.jsonl"
PRICES_REL = Path("cache") / "prices"
SIDECAR_REL = (Path("cache") / "research"
               / "research_program_validation_latest.json")
LOG_REL = Path("logs") / "research_program_validation_latest.txt"

# Horizons the production forward tracker resolves (trading days).  This
# is the CANONICAL definition — the tracker imports it from here so the
# measurement layer and the program architecture can never drift apart.
# Phase 5.1 added 15 (Tactical), 30/45 (Swing primaries), 90 (Long-Term
# diagnostic), and 126/189/252/378 (Long-Term primaries ~6/9/12/18 months)
# to the original 5/10/20/60.
TRACKED_HORIZONS = (5, 10, 15, 20, 30, 45, 60, 90, 126, 189, 252, 378)

VERDICTS = ("VALIDATED_EDGE", "PROMISING_BUT_UNPROVEN",
            "NO_EVIDENCE_OF_EDGE", "INSUFFICIENT_MATURE_EVIDENCE")

# Research lifecycle stages (Phase 5.1).  Deterministic function of the
# evidence — never advanced manually, never advanced by the LLM.
LIFECYCLE_STAGES = ("DETECTED", "RESEARCH_CANDIDATE", "UNDER_OBSERVATION",
                    "EARLY_CONFIRMATION", "VALIDATED_RESEARCH_CANDIDATE",
                    "REJECTED")

# ── program definitions (Phase 5 spec) ───────────────────────────────────────

PROGRAMS: Dict[str, Dict[str, Any]] = {
    "TACTICAL": {
        "name": "Tactical Research",
        "purpose": "Shorter-term tactical opportunities (momentum, "
                   "volume, social attention, catalysts, breakouts).",
        "expected_holding_period_td": "5-15",
        "primary_horizons_td": (5, 10, 15),
        "diagnostic_horizons_td": (),
    },
    "SWING": {
        "name": "Swing Research",
        "purpose": "Early stages of sustainable trends before broad "
                   "recognition (RS improvement, accumulation, quality "
                   "pullbacks).",
        "expected_holding_period_td": "45-60",
        "primary_horizons_td": (30, 45, 60),
        "diagnostic_horizons_td": (5, 10, 20),
    },
    "LONG_TERM": {
        "name": "Long-Term Research",
        "purpose": "Durable business quality and secular themes capable "
                   "of 6-18 month outperformance.",
        "expected_holding_period_td": "126-378",  # ~6-18 months
        "primary_horizons_td": (126, 189, 252, 378),
        "diagnostic_horizons_td": (30, 60, 90),
    },
}

# ── label -> program routing (every label is one hypothesis) ─────────────────
# (program, hypothesis, classification_reason, confidence)

LABEL_PROGRAMS: Dict[str, Dict[str, str]] = {
    "CATALYST": {
        "program": "TACTICAL",
        "hypothesis": "Named near-term catalysts produce tradable "
                      "short-horizon reactions.",
        "reason": "Catalyst-driven; the thesis expires with the event "
                  "window.",
        "confidence": "high"},
    "SOCIAL_ARB": {
        "program": "TACTICAL",
        "hypothesis": "Social attention velocity leads short-horizon "
                      "price moves before crowding.",
        "reason": "Attention signals decay in days; social-sourced.",
        "confidence": "high"},
    "NO_SOCIAL_DATA": {
        "program": "TACTICAL",
        "hypothesis": "Radar names lacking social coverage behave like "
                      "undiscovered tactical candidates.",
        "reason": "Byproduct label of the social radar; short-horizon "
                  "by construction.",
        "confidence": "low"},
    "RISKY": {
        "program": "TACTICAL",
        "hypothesis": "High-risk social names offer asymmetric "
                      "short-horizon payoffs.",
        "reason": "Social-radar risk bucket; not a durable thesis.",
        "confidence": "low"},
    "WATCH": {
        "program": "TACTICAL",
        "hypothesis": "Watch-grade social names precede tradable "
                      "attention moves.",
        "reason": "Social-radar watch bucket; short-horizon.",
        "confidence": "low"},
    "BEATEN_DOWN": {
        "program": "SWING",
        "hypothesis": "Oversold quality names recover over the "
                      "post-drawdown weeks (mean reversion into trend "
                      "repair).",
        "reason": "Recovery arcs play out over weeks, not days; entry "
                  "is tactical but the thesis is swing.",
        "confidence": "medium"},
    "EARLY_ACCUMULATION": {
        "program": "SWING",
        "hypothesis": "Volume/RS accumulation footprints precede "
                      "multi-week advances.",
        "reason": "Accumulation is an early-trend swing thesis.",
        "confidence": "high"},
    "RS_MOMENTUM_LEADER": {
        "program": "SWING",
        "hypothesis": "Relative-strength leaders persist over the "
                      "45-60 day continuation window.",
        "reason": "RS persistence is a swing-duration effect.",
        "confidence": "high"},
    "SECTOR_LEADER": {
        "program": "SWING",
        "hypothesis": "Leading names in leading sectors outperform "
                      "while the sector theme persists.",
        "reason": "Sector rotation persists for weeks to months.",
        "confidence": "medium"},
    "EXTENDED": {
        "program": "SWING",
        "hypothesis": "Extended leaders keep outperforming after "
                      "consolidation (power-trend continuation).",
        "reason": "Extension resolves over swing horizons.",
        "confidence": "low"},
    "ASYMMETRIC_RECOVERY_WATCH": {
        "program": "LONG_TERM",
        "hypothesis": "Deeply drawn-down quality businesses deliver "
                      "asymmetric multi-quarter recoveries.",
        "reason": "Category long_term_asymmetric; thesis needs quarters.",
        "confidence": "medium"},
    "SPECULATIVE_10X": {
        "program": "LONG_TERM",
        "hypothesis": "Small speculative names with secular-theme "
                      "exposure can compound multi-fold over 6-18 "
                      "months.",
        "reason": "10x outcomes are long-duration by definition.",
        "confidence": "low"},
}

# ── pre-registered verdict gates (do not tune post-hoc) ──────────────────────

PROGRAM_MIN_EPISODES = 100     # deduped ticker+label episodes
PROGRAM_MIN_DATES = 15         # distinct appearance dates
LABEL_MIN_EPISODES = 30
LABEL_MIN_DATES = 10
PROMISING_MIN_EPISODES = 10    # floor for calling anything promising
PROMISING_MIN_WIN_PCT = 55.0
VALIDATED_MIN_WIN_PCT = 52.0
BOOT_ITERATIONS = 2000
BOOT_SEED = 17


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── ledger + benchmark loading (read-only) ───────────────────────────────────


def load_ledger(root: Optional[Path] = None) -> List[Dict[str, Any]]:
    root = Path(root) if root else REPO_ROOT
    path = root / LEDGER_REL
    rows: List[Dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        if isinstance(row, dict) and row.get("ticker"):
            rows.append(row)
    return rows


class _BenchCache:
    """Close series per symbol from cache/prices (read-only)."""

    def __init__(self, root: Path):
        self.dir = root / PRICES_REL
        self._cache: Dict[str, Any] = {}

    def closes(self, symbol: str):
        if symbol in self._cache:
            return self._cache[symbol]
        series = None
        path = self.dir / f"{symbol}.parquet"
        if path.exists():
            try:
                import pandas as pd
                series = pd.read_parquet(path)["close"]
            except Exception:
                series = None
        self._cache[symbol] = series
        return series

    def fwd_return_pct(self, symbol: str, appear: str,
                       horizon: int) -> Optional[float]:
        series = self.closes(symbol)
        if series is None or not len(series):
            return None
        import pandas as pd
        idx = series.index.searchsorted(pd.Timestamp(appear))
        if idx >= len(series) or idx + horizon >= len(series):
            return None
        try:
            return float((series.iloc[idx + horizon]
                          / series.iloc[idx] - 1) * 100)
        except Exception:
            return None


# ── episode construction (dedupe: first appearance per ticker+label) ─────────


def build_episodes(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One episode per (ticker, watchlist_label): the FIRST appearance.
    Re-appearances on later nights are overlapping observations of the
    same idea, not new evidence."""
    seen: Dict[tuple, Dict[str, Any]] = {}
    for row in sorted(rows, key=lambda r: r.get("appearance_date") or ""):
        key = (row.get("ticker"), row.get("watchlist_label"))
        if key not in seen:
            seen[key] = row
    return list(seen.values())


def _excess_records(episodes: List[Dict[str, Any]], horizon: int,
                    bench: _BenchCache) -> List[Dict[str, Any]]:
    """Matured episodes at ``horizon`` with excess vs SPY/QQQ/sector."""
    out: List[Dict[str, Any]] = []
    field = f"ret_{horizon}d"
    for ep in episodes:
        ret = ep.get(field)
        if ret is None:
            continue
        appear = ep.get("appearance_date")
        rec = {"ticker": ep.get("ticker"), "appearance_date": appear,
               "ret": float(ret)}
        for name, symbol in (("spy", "SPY"), ("qqq", "QQQ"),
                             ("sector", ep.get("benchmark_sector_etf"))):
            b = bench.fwd_return_pct(symbol, appear, horizon) \
                if symbol else None
            rec[f"ex_{name}"] = (float(ret) - b) if b is not None else None
        out.append(rec)
    return out


def _cluster_boot_ci(records: List[Dict[str, Any]], key: str,
                     iterations: int = BOOT_ITERATIONS,
                     seed: int = BOOT_SEED) -> Optional[List[float]]:
    """95% CI of the mean, bootstrap-resampling appearance DATES (not
    rows) — same-night picks share regime and are not independent."""
    groups: Dict[str, List[float]] = {}
    for r in records:
        if r.get(key) is None:
            continue
        groups.setdefault(r["appearance_date"], []).append(r[key])
    dates = list(groups)
    if len(dates) < 2:
        return None
    rng = random.Random(seed)
    means: List[float] = []
    for _ in range(iterations):
        vals: List[float] = []
        for _ in range(len(dates)):
            vals.extend(groups[rng.choice(dates)])
        if vals:
            means.append(sum(vals) / len(vals))
    means.sort()
    lo = means[int(0.025 * len(means))]
    hi = means[int(0.975 * len(means)) - 1]
    return [round(lo, 2), round(hi, 2)]


def _stats(records: List[Dict[str, Any]], key: str) -> Optional[Dict]:
    vals = [r[key] for r in records if r.get(key) is not None]
    if not vals:
        return None
    vals_sorted = sorted(vals)
    n = len(vals)
    median = vals_sorted[n // 2] if n % 2 else \
        (vals_sorted[n // 2 - 1] + vals_sorted[n // 2]) / 2
    return {
        "n": n,
        "mean_pct": round(sum(vals) / n, 2),
        "median_pct": round(median, 2),
        "win_rate_pct": round(sum(1 for v in vals if v > 0) / n * 100, 1),
        "boot95_mean": _cluster_boot_ci(records, key),
    }


# ── verdict ladder (pre-registered) ──────────────────────────────────────────


def assign_verdict(horizon_stats: Dict[int, Dict[str, Any]],
                   primary_horizons: tuple,
                   min_episodes: int, min_dates: int) -> Dict[str, Any]:
    """Verdict from the LONGEST matured primary horizon; coverage floors
    must be met at primary horizons to leave the insufficient bucket."""
    matured_primary = [h for h in primary_horizons
                       if horizon_stats.get(h)
                       and horizon_stats[h].get("vs_qqq")]
    if not matured_primary:
        return {"verdict": "INSUFFICIENT_MATURE_EVIDENCE",
                "verdict_reason": "no primary evaluation horizon has "
                                  "matured forward evidence yet"}
    h = max(matured_primary)
    st = horizon_stats[h]
    qqq, sec = st.get("vs_qqq"), st.get("vs_sector")
    n = qqq["n"] if qqq else 0
    dates = st.get("n_dates") or 0
    coverage = (n >= min_episodes and dates >= min_dates
                and set(primary_horizons) <= set(matured_primary))

    def _pos(s):
        return s and s["mean_pct"] > 0

    def _ci_above_zero(s):
        return s and s.get("boot95_mean") and s["boot95_mean"][0] > 0

    if coverage and _pos(qqq) and _pos(sec) and _ci_above_zero(qqq) \
            and qqq["win_rate_pct"] >= VALIDATED_MIN_WIN_PCT:
        return {"verdict": "VALIDATED_EDGE",
                "verdict_reason": f"{h}d excess positive vs QQQ and "
                                  "sector with CI>0 at full coverage"}
    if coverage and not _pos(qqq):
        return {"verdict": "NO_EVIDENCE_OF_EDGE",
                "verdict_reason": f"coverage floors met at {h}d and "
                                  "excess vs QQQ is not positive"}
    if n >= PROMISING_MIN_EPISODES and _pos(qqq) \
            and qqq["win_rate_pct"] >= PROMISING_MIN_WIN_PCT:
        return {"verdict": "PROMISING_BUT_UNPROVEN",
                "verdict_reason": f"positive {h}d excess vs QQQ "
                                  f"(n={n}, win {qqq['win_rate_pct']}%) "
                                  "below coverage floors"}
    return {"verdict": "INSUFFICIENT_MATURE_EVIDENCE",
            "verdict_reason": f"matured {h}d evidence exists (n={n}, "
                              f"dates={dates}) but is below floors and "
                              "not clearly positive"}


def diagnostic_signal(horizon_stats: Dict[int, Dict[str, Any]],
                      diagnostic_horizons: tuple) -> Dict[str, Any]:
    """Early read from the longest matured DIAGNOSTIC horizon.  This is
    context, never a verdict — diagnostic horizons cannot validate a
    program whose thesis plays out at longer primary horizons."""
    matured = [h for h in diagnostic_horizons
               if horizon_stats.get(h) and horizon_stats[h].get("vs_qqq")]
    if not matured:
        return {"diagnostic_signal": "NONE", "diagnostic_horizon_td": None}
    h = max(matured)
    qqq = horizon_stats[h]["vs_qqq"]
    if qqq["n"] < PROMISING_MIN_EPISODES:
        signal = "TOO_FEW_EPISODES"
    elif qqq["mean_pct"] > 0 and qqq["win_rate_pct"] >= 55.0:
        signal = "POSITIVE"
    elif qqq["mean_pct"] < 0 and qqq["win_rate_pct"] <= 45.0:
        signal = "NEGATIVE"
    else:
        signal = "FLAT_OR_MIXED"
    return {"diagnostic_signal": signal, "diagnostic_horizon_td": h,
            "diagnostic_excess_vs_qqq_pct": qqq["mean_pct"],
            "diagnostic_win_rate_pct": qqq["win_rate_pct"],
            "diagnostic_n": qqq["n"]}


def lifecycle_stage(n_episodes: int, horizon_stats: Dict[int, Dict],
                    verdict: str, diag_signal: str) -> str:
    """Deterministic lifecycle stage from evidence state alone:
    DETECTED -> RESEARCH_CANDIDATE -> UNDER_OBSERVATION ->
    EARLY_CONFIRMATION -> VALIDATED_RESEARCH_CANDIDATE, or REJECTED
    once coverage floors are met with no edge."""
    if verdict == "VALIDATED_EDGE":
        return "VALIDATED_RESEARCH_CANDIDATE"
    if verdict == "NO_EVIDENCE_OF_EDGE":
        return "REJECTED"
    if verdict == "PROMISING_BUT_UNPROVEN" or diag_signal == "POSITIVE":
        return "EARLY_CONFIRMATION"
    if horizon_stats:
        return "UNDER_OBSERVATION"
    if n_episodes > 0:
        return "RESEARCH_CANDIDATE"
    return "DETECTED"


# ── routing API ──────────────────────────────────────────────────────────────


def classify_label(label: str) -> Dict[str, Any]:
    """Routing record for one watchlist label (static classification)."""
    route = LABEL_PROGRAMS.get(label)
    if route is None:
        return {"label": label, "program": "UNROUTED",
                "hypothesis": None, "reason": "label not in routing map",
                "confidence": "none",
                "expected_holding_period_td": None,
                "evaluation_horizons_td": [],
                "diagnostic_horizons_td": []}
    prog = PROGRAMS[route["program"]]
    return {
        "label": label,
        "program": route["program"],
        "program_name": prog["name"],
        "hypothesis": route["hypothesis"],
        "reason": route["reason"],
        "confidence": route["confidence"],
        "expected_holding_period_td": prog["expected_holding_period_td"],
        "evaluation_horizons_td": list(prog["primary_horizons_td"]),
        "diagnostic_horizons_td": list(prog["diagnostic_horizons_td"]),
    }


def horizon_coverage(primary: tuple, diagnostic: tuple) -> Dict[str, Any]:
    """Which of a program's horizons the tracker collects at all."""
    tracked = set(TRACKED_HORIZONS)
    return {
        "tracked_horizons_td": list(TRACKED_HORIZONS),
        "primary_collected": sorted(set(primary) & tracked),
        "primary_not_collected": sorted(set(primary) - tracked),
        "diagnostic_collected": sorted(set(diagnostic) & tracked),
        "diagnostic_not_collected": sorted(set(diagnostic) - tracked),
    }


# ── validation build ─────────────────────────────────────────────────────────


def _horizon_block(episodes: List[Dict[str, Any]],
                   horizons: tuple, bench: _BenchCache) -> Dict[int, Dict]:
    out: Dict[int, Dict[str, Any]] = {}
    for h in horizons:
        if h not in TRACKED_HORIZONS:
            continue
        recs = _excess_records(episodes, h, bench)
        if not recs:
            continue
        out[h] = {
            "n_episodes": len(recs),
            "n_dates": len({r["appearance_date"] for r in recs}),
            "raw_ret": _stats(recs, "ret"),
            "vs_spy": _stats(recs, "ex_spy"),
            "vs_qqq": _stats(recs, "ex_qqq"),
            "vs_sector": _stats(recs, "ex_sector"),
        }
    return out


def build_program_validation(root: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(root) if root else REPO_ROOT
    rows = load_ledger(root)
    episodes = build_episodes(rows)
    bench = _BenchCache(root)

    ledger_labels = sorted({r.get("watchlist_label") for r in rows
                            if r.get("watchlist_label")})
    unrouted = [l for l in ledger_labels if l not in LABEL_PROGRAMS]

    programs_out: Dict[str, Any] = {}
    for pid, prog in PROGRAMS.items():
        labels = [l for l, r in LABEL_PROGRAMS.items()
                  if r["program"] == pid]
        prog_eps = [e for e in episodes
                    if e.get("watchlist_label") in labels]
        all_h = tuple(sorted(set(prog["primary_horizons_td"])
                             | set(prog["diagnostic_horizons_td"])))
        hstats = _horizon_block(prog_eps, all_h, bench)
        verdict = assign_verdict(
            hstats, prog["primary_horizons_td"],
            PROGRAM_MIN_EPISODES, PROGRAM_MIN_DATES)

        label_blocks = {}
        for label in labels:
            leps = [e for e in prog_eps
                    if e.get("watchlist_label") == label]
            if not leps:
                continue
            lstats = _horizon_block(leps, all_h, bench)
            lverdict = assign_verdict(
                lstats, prog["primary_horizons_td"],
                LABEL_MIN_EPISODES, LABEL_MIN_DATES)
            ldiag = diagnostic_signal(lstats, prog["diagnostic_horizons_td"])
            label_blocks[label] = {
                **classify_label(label),
                "n_episodes": len(leps),
                "horizons": {str(h): v for h, v in lstats.items()},
                **lverdict,
                **ldiag,
                "lifecycle_stage": lifecycle_stage(
                    len(leps), lstats, lverdict["verdict"],
                    ldiag["diagnostic_signal"]),
            }

        programs_out[pid] = {
            "name": prog["name"],
            "purpose": prog["purpose"],
            "expected_holding_period_td":
                prog["expected_holding_period_td"],
            "labels": labels,
            "n_episodes": len(prog_eps),
            "horizon_coverage": horizon_coverage(
                prog["primary_horizons_td"],
                prog["diagnostic_horizons_td"]),
            "horizons": {str(h): v for h, v in hstats.items()},
            **verdict,
            **diagnostic_signal(hstats, prog["diagnostic_horizons_td"]),
            "label_verdicts": label_blocks,
        }

    return {
        "kind": "research_program_validation",
        "version": 1,
        "generated_at": _utcnow(),
        "research_only": True,
        "guardrails": {
            "no_trade_recommendation": True,
            "no_production_change": True,
            "pre_registered_gates": True,
        },
        "ledger_rows": len(rows),
        "deduped_episodes": len(episodes),
        "duplication_factor": round(len(rows) / max(len(episodes), 1), 2),
        "unrouted_labels": unrouted,
        "gates": {
            "program_min_episodes": PROGRAM_MIN_EPISODES,
            "program_min_dates": PROGRAM_MIN_DATES,
            "label_min_episodes": LABEL_MIN_EPISODES,
            "label_min_dates": LABEL_MIN_DATES,
            "promising_min_episodes": PROMISING_MIN_EPISODES,
            "promising_min_win_pct": PROMISING_MIN_WIN_PCT,
            "validated_min_win_pct": VALIDATED_MIN_WIN_PCT,
        },
        "programs": programs_out,
    }


# ── rendering + persistence ──────────────────────────────────────────────────


def render_text(report: Dict[str, Any]) -> str:
    lines = ["RESEARCH PROGRAM VALIDATION (research-only; "
             "pre-registered gates)",
             f"  generated: {report['generated_at'][:16]} | ledger rows "
             f"{report['ledger_rows']} -> episodes "
             f"{report['deduped_episodes']} "
             f"(x{report['duplication_factor']} duplication)"]
    if report["unrouted_labels"]:
        lines.append(f"  UNROUTED LABELS: {report['unrouted_labels']}")
    for pid, p in report["programs"].items():
        cov = p["horizon_coverage"]
        lines.append(f"\n{pid} — {p['name']} (hold "
                     f"{p['expected_holding_period_td']}td, "
                     f"episodes {p['n_episodes']})")
        lines.append(f"  VERDICT: {p['verdict']} — {p['verdict_reason']}")
        if cov["primary_not_collected"]:
            lines.append("  horizon gap: primary "
                         f"{cov['primary_not_collected']}td not collected "
                         "by the forward tracker")
        for h, st in sorted(p["horizons"].items(), key=lambda kv: int(kv[0])):
            q = st.get("vs_qqq") or {}
            lines.append(
                f"    {h:>3}d: n={st['n_episodes']:3} "
                f"dates={st['n_dates']:2} "
                f"exQQQ mean {q.get('mean_pct', 'n/a')}% "
                f"med {q.get('median_pct', 'n/a')}% "
                f"win {q.get('win_rate_pct', 'n/a')}% "
                f"ci {q.get('boot95_mean')}")
        for label, lb in p["label_verdicts"].items():
            diag = lb.get("diagnostic_signal")
            note = ""
            if diag and diag not in ("NONE",):
                note = (f" diag@{lb.get('diagnostic_horizon_td')}d="
                        f"{diag}")
                if lb.get("diagnostic_excess_vs_qqq_pct") is not None:
                    note += (f" ({lb['diagnostic_excess_vs_qqq_pct']:+}% "
                             f"exQQQ, win "
                             f"{lb['diagnostic_win_rate_pct']}%, "
                             f"n={lb['diagnostic_n']})")
            lines.append(f"    - {label:26} {lb['verdict']:30} "
                         f"(n={lb['n_episodes']}){note}")
    return "\n".join(lines)


def write_outputs(report: Dict[str, Any],
                  root: Optional[Path] = None) -> Dict[str, Path]:
    root = Path(root) if root else REPO_ROOT
    sidecar = root / SIDECAR_REL
    log = root / LOG_REL
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    log.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(json.dumps(report, ensure_ascii=False, indent=2)
                       + "\n", encoding="utf-8")
    log.write_text(render_text(report) + "\n", encoding="utf-8")
    return {"sidecar": sidecar, "log": log}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Horizon-aligned research-program validation "
                    "(research-only, read-only).")
    parser.add_argument("--dry-run", action="store_true",
                        help="print only; write nothing")
    parser.add_argument("--json", action="store_true",
                        help="print full JSON instead of the text view")
    parser.add_argument("--root", default=None, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    root = Path(args.root) if args.root else REPO_ROOT

    report = build_program_validation(root)
    print(json.dumps(report, ensure_ascii=False, indent=2)
          if args.json else render_text(report))
    if not args.dry_run:
        paths = write_outputs(report, root)
        print(f"\nwritten: {paths['sidecar']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
