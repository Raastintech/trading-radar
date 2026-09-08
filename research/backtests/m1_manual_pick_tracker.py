"""Forward tracker for a HUMAN-SELECTED subset of the M1 shadow pool.

What this is
------------
The M1 shadow pool (``m1_shadow_pool``) publishes membership and refuses to
order it, because M1's edge is a basket property: its ticker-clustered
confidence interval is negative at every horizon tested, so the basket beats
the tape while the typical name inside it does not. Thirteen conviction
overlays, an earnings-surprise dataset and a complete analyst-action study all
failed to rank names inside it.

A human then picked ten of the seventy-five anyway. This module exists to find
out whether that was worth anything, and it is built so the answer can come
back "no".

The control arm is what makes it a test
---------------------------------------
The picks are measured against the SIXTY-FIVE NAMES NOT PICKED, not only
against SPY/QQQ. Beating SPY/QQQ would prove nothing about selection: the pool
already beats the tape by construction, so an index comparison would credit the
selector for M1's basket property. Only the not-picked arm isolates the claim
"choosing inside M1 adds information".

Stages
------
``freeze``   Write-once. Reads the human pick file and the pool's forward rows,
             stamps reference closes, and records the hypothesis and its
             falsification condition BEFORE any outcome is known. Refuses to
             overwrite an existing cohort unless ``--refreeze`` is passed,
             because a cohort that can be edited after the fact measures
             nothing.
``resolve``  Cache-only. Fills in forward returns for whichever horizons have
             matured and publishes a verdict. Immature horizons stay null.

Writes ONLY:
    cache/research/m1_manual_picks_{session}.json          (freeze)
    cache/research/m1_manual_picks_resolution_latest.json  (resolve)
    logs/m1_manual_picks_latest.txt

Zero provider calls in either stage: every price comes from the replay cache.

Usage::

    GEM_TRADER_SKIP_DOTENV=true .venv/bin/python -m research.backtests.m1_manual_pick_tracker freeze
    GEM_TRADER_SKIP_DOTENV=true .venv/bin/python -m research.backtests.m1_manual_pick_tracker resolve
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from research.backtests.common import (
    LiveArtifactTripwire,
    ROOT,
    add_safety_args,
    offline_env,
    run_cli,
    write_replay_json,
    write_replay_text,
)

offline_env()

PRICES_REL = "cache/replay_prices"
POOL_ROWS_REL = "cache/research/m1_shadow_pool_forward_rows.jsonl"
INPUT_REL = "research/backtests/m1_manual_picks_2026_09_04.json"
COHORT_REL_FMT = "cache/research/m1_manual_picks_{session}.json"
RESOLUTION_REL = "cache/research/m1_manual_picks_resolution_latest.json"
REPORT_TXT_REL = "logs/m1_manual_picks_latest.txt"

HORIZONS = (20, 45, 60, 90)
BENCHMARKS = ("SPY", "QQQ")

#: Horizons that must BOTH favour the picks, on BOTH mean and median, before
#: selection is credited with anything. Pre-registered, not chosen after seeing
#: the numbers. 20d is deliberately excluded: it is the noisiest horizon and the
#: one most likely to hand back a false positive.
DECISIVE_HORIZONS = (45, 60)

NOTE = (
    "M1 MANUAL PICK TRACKER — RESEARCH ONLY. A falsification test of HUMAN "
    "selection inside the M1 shadow pool, measured against the names NOT "
    "picked. Emits no signal, no ranking, no trade recommendation and no live "
    "verdict. NOT live forward evidence and NOT backtest evidence for Phase 4B. "
    "Must never be pooled with the live forward ledger, program verdicts, "
    "HC/EO routing, Alpha Focus, MCP or the dashboard."
)


class CohortExists(RuntimeError):
    """Raised when a freeze would overwrite a cohort that already exists."""


# ── shared helpers ──────────────────────────────────────────────────────────


def load_pool_rows(root: Path) -> list[dict[str, Any]]:
    path = root / POOL_ROWS_REL
    if not path.exists():
        raise FileNotFoundError(
            f"{POOL_ROWS_REL} is missing — run the shadow pool first; this "
            "module never invents its own universe.")
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _series(root: Path, ticker: str):
    import pandas as pd  # noqa: PLC0415

    path = root / PRICES_REL / f"{ticker}.parquet"
    if not path.exists():
        return None
    try:
        return pd.read_parquet(path).sort_index()
    except Exception:
        return None


def forward_return_pct(root: Path, ticker: str, reference_bar: str,
                       sessions: int) -> float | None:
    """Return over `sessions` bars after the reference bar, or None if immature.

    Immature and missing are both None on purpose: a horizon that has not
    elapsed must not be silently reported as a zero, which is the single
    easiest way to fake a flat result into an edge.
    """
    import pandas as pd  # noqa: PLC0415

    df = _series(root, ticker)
    if df is None or "close" not in df:
        return None
    idx = df.index
    ref = pd.Timestamp(reference_bar)
    pos = idx.searchsorted(ref)
    if pos >= len(idx) or idx[pos] != ref:
        return None
    if pos + sessions >= len(idx):
        return None                      # horizon has not elapsed yet
    start = float(df["close"].iloc[pos])
    end = float(df["close"].iloc[pos + sessions])
    if start <= 0:
        return None
    return (end / start - 1.0) * 100.0


def _stats(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0, "mean": None, "median": None}
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    median = ordered[mid] if n % 2 else (ordered[mid - 1] + ordered[mid]) / 2.0
    return {"n": n, "mean": round(sum(ordered) / n, 3), "median": round(median, 3)}


# ── freeze (write-once) ─────────────────────────────────────────────────────


def build_cohort(root: Path, *, now: datetime | None = None) -> dict[str, Any]:
    """Assemble the pre-registered cohort. Reads only; writes nothing."""
    spec = json.loads((root / INPUT_REL).read_text())
    rows = {r["ticker"]: r for r in load_pool_rows(root)}
    session = spec["session"]

    picks, missing = [], []
    for entry in spec["picks"]:
        t = entry["ticker"]
        row = rows.get(t)
        if row is None:
            missing.append(t)
            continue
        picks.append({**entry,
                      "reference_close": row["reference_close"],
                      "reference_bar": row["reference_bar"]})
    if missing:
        raise ValueError(
            f"picks absent from the published pool: {missing}. A pick must come "
            "from the pool the report published, or the test is measuring "
            "something else.")

    picked = {p["ticker"] for p in picks}
    control = [{"ticker": t, "reference_close": r["reference_close"],
                "reference_bar": r["reference_bar"]}
               for t, r in sorted(rows.items()) if t not in picked]

    return {
        "kind": "M1_MANUAL_PICK_COHORT",
        "session": session,
        "frozen_at": (now or datetime.now(timezone.utc)).isoformat(),
        "note": NOTE,
        "prior": spec["prior"],
        "selection_rule": spec["selection_rule"],
        "hypothesis": spec["hypothesis"],
        "falsification": spec["falsification"],
        "decisive_horizons": list(DECISIVE_HORIZONS),
        "known_weaknesses": spec["known_weaknesses"],
        "horizons_sessions": list(HORIZONS),
        "benchmarks": list(BENCHMARKS),
        "picks": picks,
        "control_not_picked": control,
        "n_picks": len(picks),
        "n_control": len(control),
        "control_is_the_test": (
            "The picks are judged against the names NOT picked. Beating "
            "SPY/QQQ is reported but is not evidence about selection: the pool "
            "beats the tape by construction, so an index comparison would "
            "credit the selector for M1's basket property."
        ),
        "write_once": True,
        "not_live_evidence": True,
        "not_phase4b": True,
        "research_only": True,
    }


def freeze_stage(args) -> int:
    root = Path(args.root)
    tripwire = LiveArtifactTripwire.snapshot(root)
    cohort = build_cohort(root)
    dest = root / COHORT_REL_FMT.format(session=cohort["session"])
    if dest.exists() and not args.refreeze:
        raise CohortExists(
            f"{dest.relative_to(root)} already exists. A cohort that can be "
            "rewritten after outcomes are known measures nothing — pass "
            "--refreeze only to correct a cohort no outcome has been read "
            "from yet.")
    write_replay_json(dest, cohort, root=root)
    print(render_freeze(cohort, dest.relative_to(root)))
    print(f"\n{tripwire.report()}")
    tripwire.assert_clean()
    return 0


def render_freeze(cohort: dict[str, Any], rel: Path | str) -> str:
    L = ["=" * 78, "M1 MANUAL PICK COHORT — FROZEN (zero provider calls)", "=" * 78,
         NOTE, "",
         f"  session                    {cohort['session']}",
         f"  picks                      {cohort['n_picks']}",
         f"  control (not picked)       {cohort['n_control']}",
         f"  horizons (sessions)        {cohort['horizons_sessions']}",
         f"  decisive horizons          {cohort['decisive_horizons']}",
         f"  written                    {rel}", "",
         "  HYPOTHESIS", f"    {cohort['hypothesis']}", "",
         "  FALSIFICATION", f"    {cohort['falsification']}", "",
         "  PICKS"]
    for p in cohort["picks"]:
        L.append(f"    {p['ticker']:<6} {p['sector']:<20} ref {p['reference_close']}")
    L += ["", "  KNOWN WEAKNESSES"]
    L += [f"    - {w}" for w in cohort["known_weaknesses"]]
    L.append("=" * 78)
    return "\n".join(L)


# ── resolve (cache-only) ────────────────────────────────────────────────────


def resolve_cohort(root: Path, cohort: dict[str, Any]) -> dict[str, Any]:
    """Fill in whatever has matured. Immature horizons stay null."""
    per_name: dict[str, dict[str, Any]] = {}
    arms: dict[str, dict[str, list[float]]] = {"picks": {}, "control": {}}

    for arm, key in (("picks", "picks"), ("control", "control_not_picked")):
        for entry in cohort[key]:
            t = entry["ticker"]
            got: dict[str, Any] = {}
            for h in cohort["horizons_sessions"]:
                r = forward_return_pct(root, t, entry["reference_bar"], h)
                got[f"{h}d"] = None if r is None else round(r, 3)
                if r is not None:
                    arms[arm].setdefault(f"{h}d", []).append(r)
            per_name[t] = {"arm": arm, "returns_pct": got}

    bench: dict[str, dict[str, Any]] = {}
    ref_bar = cohort["picks"][0]["reference_bar"] if cohort["picks"] else None
    for b in cohort["benchmarks"]:
        bench[b] = {f"{h}d": (None if ref_bar is None else
                              (lambda v: None if v is None else round(v, 3))(
                                  forward_return_pct(root, b, ref_bar, h)))
                    for h in cohort["horizons_sessions"]}

    horizons: dict[str, Any] = {}
    for h in cohort["horizons_sessions"]:
        k = f"{h}d"
        p, c = _stats(arms["picks"].get(k, [])), _stats(arms["control"].get(k, []))
        mature = p["n"] > 0 and c["n"] > 0
        horizons[k] = {
            "picks": p, "control_not_picked": c,
            "benchmarks": {b: bench[b][k] for b in cohort["benchmarks"]},
            "mature": mature,
            "picks_beat_control_mean": (
                None if not mature else p["mean"] > c["mean"]),
            "picks_beat_control_median": (
                None if not mature else p["median"] > c["median"]),
            "edge_vs_control_mean_pp": (
                None if not mature else round(p["mean"] - c["mean"], 3)),
        }

    return {"per_name": per_name, "horizons": horizons}


def verdict(cohort: dict[str, Any], resolved: dict[str, Any]) -> dict[str, Any]:
    """The pre-registered ladder. NO_EDGE is the default, not the exception."""
    decisive = [f"{h}d" for h in cohort["decisive_horizons"]]
    states = [resolved["horizons"][k] for k in decisive if k in resolved["horizons"]]
    if not states or not all(s["mature"] for s in states):
        matured = [k for k in decisive
                   if resolved["horizons"].get(k, {}).get("mature")]
        return {
            "verdict": "NOT_MATURE",
            "reason": (
                f"decisive horizons {decisive} are not all resolved "
                f"(matured: {matured or 'none'}). No claim can be made, in "
                "either direction."),
            "may_conclude": False,
        }
    both = all(s["picks_beat_control_mean"] and s["picks_beat_control_median"]
               for s in states)
    if both:
        return {
            "verdict": "SELECTION_ADDS_VALUE_PROVISIONAL",
            "reason": (
                "picks beat the not-picked arm on mean AND median at every "
                f"decisive horizon {decisive}. Provisional only: one session, "
                f"{cohort['n_picks']} names, one cohort. It promotes nothing "
                "and must be replicated on further sessions before it is more "
                "than an anecdote."),
            "may_conclude": False,
        }
    return {
        "verdict": "NO_EDGE_FROM_SELECTION",
        "reason": (
            "the picks did not beat the not-picked arm on both mean and median "
            f"at every decisive horizon {decisive}. This is the prior, and it "
            "stands: choosing inside M1 added nothing."),
        "may_conclude": True,
    }


def resolve_stage(args) -> int:
    root = Path(args.root)
    tripwire = LiveArtifactTripwire.snapshot(root)
    session = args.session
    dest = root / COHORT_REL_FMT.format(session=session)
    if not dest.exists():
        raise FileNotFoundError(
            f"no frozen cohort at {dest.relative_to(root)} — run `freeze` first. "
            "Resolution never invents a cohort, because a cohort chosen after "
            "the outcome is not a test.")
    cohort = json.loads(dest.read_text())
    resolved = resolve_cohort(root, cohort)
    out = {
        "kind": "M1_MANUAL_PICK_RESOLUTION",
        "session": cohort["session"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note": NOTE,
        "hypothesis": cohort["hypothesis"],
        "falsification": cohort["falsification"],
        "n_picks": cohort["n_picks"],
        "n_control": cohort["n_control"],
        **resolved,
        **verdict(cohort, resolved),
        "provider_calls": 0,
        "not_live_evidence": True,
        "not_phase4b": True,
        "research_only": True,
    }
    write_replay_json(root / RESOLUTION_REL, out, root=root)
    text = render_resolution(cohort, out)
    write_replay_text(root / REPORT_TXT_REL, text, root=root)
    print(text)
    print(f"\n{tripwire.report()}")
    tripwire.assert_clean()
    return 0


def render_resolution(cohort: dict[str, Any], out: dict[str, Any]) -> str:
    L = ["=" * 78, "M1 MANUAL PICK TRACKER — RESOLUTION", "=" * 78, NOTE, "",
         f"  session                    {out['session']}",
         f"  picks / control            {out['n_picks']} / {out['n_control']}", "",
         "  HYPOTHESIS", f"    {cohort['hypothesis']}", "",
         "-" * 78,
         f"  {'horizon':<10}{'picks mean':>12}{'control mean':>14}"
         f"{'edge pp':>10}{'SPY':>9}{'QQQ':>9}  state",
         "-" * 78]
    for h in cohort["horizons_sessions"]:
        k, s = f"{h}d", out["horizons"][f"{h}d"]
        fmt = lambda v: "—" if v is None else f"{v:+.2f}"
        state = "resolved" if s["mature"] else "not yet elapsed"
        L.append(f"  {k:<10}{fmt(s['picks']['mean']):>12}"
                 f"{fmt(s['control_not_picked']['mean']):>14}"
                 f"{fmt(s['edge_vs_control_mean_pp']):>10}"
                 f"{fmt(s['benchmarks'].get('SPY')):>9}"
                 f"{fmt(s['benchmarks'].get('QQQ')):>9}  {state}")
    L += ["", f"  VERDICT   {out['verdict']}", f"    {out['reason']}", "",
          "  The control arm is the test. Beating SPY/QQQ is reported but is",
          "  not evidence about selection — the pool beats the tape by",
          "  construction.", "=" * 78]
    return "\n".join(L)


# ── CLI ─────────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("stage", nargs="?", default="resolve", choices=["freeze", "resolve"])
    p.add_argument("--session", default="2026-09-04",
                   help="cohort session to resolve.")
    p.add_argument("--refreeze", action="store_true",
                   help="overwrite an existing cohort. Only legitimate before "
                        "any outcome has been read from it.")
    add_safety_args(p, fetches=False)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    stage = freeze_stage if args.stage == "freeze" else resolve_stage
    try:
        return run_cli(stage, args)
    except CohortExists as e:
        # An expected operator outcome, not a crash: refusing to overwrite a
        # frozen cohort is the module working, so it exits like a refused fetch
        # rather than a traceback.
        print(f"\nREFUSED: {e}")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
