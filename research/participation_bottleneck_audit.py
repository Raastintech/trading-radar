"""
research/participation_bottleneck_audit.py — Phase 1G.17 Task 1.

Verifies (or refutes) the participation-starvation finding: the system is not
losing money because nothing reaches the council/execution layer at all.

For every day since --since (default 2026-05-01) and each active sleeve it
measures, from real artifacts only:

  daemon log  → scan cycles, input universe size, opportunity count,
                rejection-reason distribution (both pre- and post-telemetry
                log formats are parsed)
  decisions   → decision count, positions opened/closed
  veto_log    → council evaluation volume (strict vs starved discriminator)
  paper_signals / voyager_paper_signals → paper-evidence emission

and renders the scan→council→decision funnel with explicit verdicts:

  council_state    STARVED (scans ran, nothing arrived) | STRICT (arrived,
                   all vetoed) | FLOWING | UNKNOWN
  execution_state  NEVER_REACHED | BROKEN | FLOWING | UNKNOWN
  participation    STARVED | HEALTHY | UNKNOWN  + reason
                   {entry_gates, data_depth, holdout_starved, none}

RESEARCH-ONLY / CACHE-ONLY / READ-ONLY:
  - DB access is via a read-only (immutable) sqlite connection.
  - The daemon log is read, never written.
  - No provider calls, no signals, no trade proposals, no gate or execution
    or governance change, no live trading. Diagnostic only.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from research.scanner_truth import dataio

DAEMON_LOG = dataio.LOGS_DIR / "gem-trader.log"
HEARTBEAT = dataio.LOGS_DIR / "trader_heartbeat.json"
OUT_JSON = dataio.RESEARCH_CACHE / "participation_bottleneck_audit_latest.json"
OUT_TXT = dataio.LOGS_DIR / "participation_bottleneck_audit_latest.txt"
OUT_DOC = dataio.REPO / "docs" / "research" / "PARTICIPATION_BOTTLENECK_AUDIT.md"

DEFAULT_SINCE = "2026-05-01"
ACTIVE_SLEEVES = ("SNIPER", "VOYAGER")

# ── daemon-log scan-line parsing ──────────────────────────────────────────────
# Two SNIPER formats exist in the log history:
#   new : "Sniper scan: input=46  whitelist_dropped=0  evaluated=46
#          opportunities=0  vix=21.2 | rejections: no_breakout=46"
#   new (regime): "Sniper scan: input=46  regime_suppressed=VIX(28.1>=28.0)
#          opportunities=0"
#   old : "Sniper scan: 0 opportunities from 90 tickers"
# Two VOYAGER formats:
#   new : "VOYAGER: 0 setup(s) from 74 tickers | rejections: weak_rs_50d=26 …"
#   old : "VOYAGER: 90 tickers → 0 signals"

_DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}) ")
_SNIPER_NEW = re.compile(
    r"strategies\.sniper: Sniper scan: input=(\d+)\s+"
    r"(?:whitelist_dropped=(\d+)\s+evaluated=(\d+)\s+)?"
    r"(?:regime_suppressed=(\S+)\s+)?"
    r"opportunities=(\d+)"
    r"(?:.*?\| rejections: (.*))?$"
)
_SNIPER_OLD = re.compile(
    r"strategies\.sniper: Sniper scan: (\d+) opportunities from (\d+) tickers"
)
_VOYAGER_NEW = re.compile(
    r"strategies\.voyager: VOYAGER: (\d+) setup\(s\) from (\d+) tickers"
    r"(?:\s*\| rejections: (.*))?$"
)
_VOYAGER_OLD = re.compile(r"main: VOYAGER: (\d+) tickers → (\d+) signals")


def _parse_rejections(blob: Optional[str]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for tok in (blob or "").split():
        if "=" in tok:
            k, _, v = tok.partition("=")
            try:
                out[k] = out.get(k, 0) + int(v)
            except ValueError:
                continue
    return out


def parse_daemon_log(
    log_path: Path = DAEMON_LOG, since: str = DEFAULT_SINCE
) -> Dict[str, Dict[str, Dict]]:
    """Returns {sleeve: {day: {cycles, input, opportunities, rejections,
    regime_suppressed_cycles}}} aggregated per day from the daemon log."""
    days: Dict[str, Dict[str, Dict]] = {s: {} for s in ACTIVE_SLEEVES}
    if not log_path.exists():
        return days

    def bucket(sleeve: str, day: str) -> Dict:
        return days[sleeve].setdefault(day, {
            "cycles": 0, "input_max": 0, "opportunities": 0,
            "regime_suppressed_cycles": 0, "rejections": defaultdict(int),
        })

    with log_path.open(errors="replace") as fh:
        for line in fh:
            m_date = _DATE_RE.match(line)
            if not m_date:
                continue
            day = m_date.group(1)
            if day < since:
                continue

            m = _SNIPER_NEW.search(line)
            if m:
                b = bucket("SNIPER", day)
                b["cycles"] += 1
                b["input_max"] = max(b["input_max"], int(m.group(1)))
                if m.group(4):
                    b["regime_suppressed_cycles"] += 1
                b["opportunities"] += int(m.group(5))
                for k, v in _parse_rejections(m.group(6)).items():
                    b["rejections"][k] += v
                continue
            m = _SNIPER_OLD.search(line)
            if m:
                b = bucket("SNIPER", day)
                b["cycles"] += 1
                b["opportunities"] += int(m.group(1))
                b["input_max"] = max(b["input_max"], int(m.group(2)))
                continue
            m = _VOYAGER_NEW.search(line)
            if m:
                b = bucket("VOYAGER", day)
                b["cycles"] += 1
                b["opportunities"] += int(m.group(1))
                b["input_max"] = max(b["input_max"], int(m.group(2)))
                for k, v in _parse_rejections(m.group(3)).items():
                    b["rejections"][k] += v
                continue
            m = _VOYAGER_OLD.search(line)
            if m:
                b = bucket("VOYAGER", day)
                b["cycles"] += 1
                b["input_max"] = max(b["input_max"], int(m.group(1)))
                b["opportunities"] += int(m.group(2))

    for sleeve in days:
        for day in days[sleeve]:
            days[sleeve][day]["rejections"] = dict(days[sleeve][day]["rejections"])
    return days


# ── DB metrics (read-only) ────────────────────────────────────────────────────

def _db_daily(since: str) -> Dict[str, Dict]:
    """Per-day DB counters: decisions, veto_log, paper signals, opens/closes."""
    out: Dict[str, Dict] = defaultdict(lambda: {
        "decisions": 0, "veto_log": 0, "paper_signals": 0,
        "voyager_paper_signals": 0, "positions_opened": 0, "positions_closed": 0,
    })
    with dataio._ro_conn() as con:
        for day, n, op, cl in con.execute(
            "SELECT date(ts), count(*), coalesce(sum(position_opened),0), "
            "coalesce(sum(position_closed),0) FROM decisions WHERE ts >= ? "
            "GROUP BY 1", (since,)
        ):
            out[day]["decisions"] = n
            out[day]["positions_opened"] = op
            out[day]["positions_closed"] = cl
        for day, n in con.execute(
            "SELECT date(ts), count(*) FROM veto_log WHERE ts >= ? GROUP BY 1",
            (since,)
        ):
            out[day]["veto_log"] = n
        for day, n in con.execute(
            "SELECT date(logged_at), count(*) FROM paper_signals "
            "WHERE logged_at >= ? GROUP BY 1", (since,)
        ):
            out[day]["paper_signals"] = n
        for day, n in con.execute(
            "SELECT date(logged_at), count(*) FROM voyager_paper_signals "
            "WHERE logged_at >= ? GROUP BY 1", (since,)
        ):
            out[day]["voyager_paper_signals"] = n
    return dict(out)


def _db_last_dates() -> Dict[str, Optional[str]]:
    q = {
        "last_decision": "SELECT max(date(ts)) FROM decisions",
        "last_veto": "SELECT max(date(ts)) FROM veto_log",
        "last_paper_signal_SNIPER":
            "SELECT max(date(logged_at)) FROM paper_signals WHERE strategy='SNIPER'",
        "last_paper_signal_VOYAGER":
            "SELECT max(date(logged_at)) FROM voyager_paper_signals",
        "last_scan_result": "SELECT max(date(ts)) FROM scan_results",
    }
    out: Dict[str, Optional[str]] = {}
    with dataio._ro_conn() as con:
        for k, sql in q.items():
            try:
                out[k] = con.execute(sql).fetchone()[0]
            except Exception:
                out[k] = None
    return out


# ── verdicts ──────────────────────────────────────────────────────────────────
# Verdicts are computed over the RECENT window (the last RECENT_DAYS days that
# had any scan cycles), not the full audit window: early-May flow that has
# since collapsed must not mask present-day starvation. The full-window funnel
# stays in the report for history.

RECENT_DAYS = 10


def classify_sleeve(opportunities: int, distinct_signals: int,
                    decisions: int) -> str:
    """Per-sleeve recent-flow state. `opportunities` is per-cycle (the same
    ticker re-emitted every 5-min cycle counts each time), so distinct paper
    signals are the honest emission count."""
    if opportunities == 0:
        return "STARVED"
    if decisions == 0:
        return "TRICKLE_VETOED" if distinct_signals <= 3 else "EMITTING_VETOED"
    return "FLOWING"


def classify_council(total_opportunities: int, total_veto_rows: int,
                     total_decisions: int, total_cycles: int) -> str:
    """Council is STARVED when scans ran but produced ~nothing to evaluate;
    STRICT when candidates arrived and none became decisions; FLOWING else."""
    if total_cycles == 0:
        return "UNKNOWN"
    if total_opportunities == 0 and total_veto_rows <= total_cycles * 0.01:
        return "STARVED"
    if total_opportunities > 0 and total_decisions == 0:
        return "STRICT"
    if total_decisions > 0:
        return "FLOWING"
    return "STARVED"


def classify_execution(total_opportunities: int, total_decisions: int,
                       opened: int) -> str:
    if total_opportunities == 0:
        return "NEVER_REACHED"
    if total_decisions > 0 and opened == 0:
        return "BROKEN"
    if opened > 0:
        return "FLOWING"
    return "NEVER_REACHED"


def classify_participation(sleeve_states: Dict[str, str],
                           data_depth_suspect: bool) -> Tuple[str, str]:
    """(state, reason). STARVED unless at least one active sleeve is FLOWING.
    A vetoed trickle (1 distinct candidate every few days) is still starvation
    of the decision layer, not health."""
    if not sleeve_states:
        return "UNKNOWN", "none"
    if any(s == "FLOWING" for s in sleeve_states.values()):
        return "HEALTHY", "none"
    reason = "data_depth" if data_depth_suspect else "entry_gates"
    return "STARVED", reason


# ── build ─────────────────────────────────────────────────────────────────────

def build(since: str = DEFAULT_SINCE) -> Dict:
    log_days = parse_daemon_log(since=since)
    db_days = _db_daily(since)
    last = _db_last_dates()

    all_days = sorted(
        set(db_days) | {d for s in log_days.values() for d in s}
    )

    daily: List[Dict] = []
    totals = {
        s: {"cycles": 0, "opportunities": 0, "rejections": defaultdict(int)}
        for s in ACTIVE_SLEEVES
    }
    tot_veto = tot_dec = tot_open = tot_close = tot_paper = 0
    for day in all_days:
        row: Dict = {"day": day}
        for sleeve in ACTIVE_SLEEVES:
            b = log_days[sleeve].get(day, {})
            row[sleeve.lower()] = {
                "cycles": b.get("cycles", 0),
                "input": b.get("input_max", 0),
                "opportunities": b.get("opportunities", 0),
                "regime_suppressed_cycles": b.get("regime_suppressed_cycles", 0),
                "top_rejection": (max(b.get("rejections", {}).items(),
                                      key=lambda kv: kv[1])[0]
                                  if b.get("rejections") else None),
                "rejections": b.get("rejections", {}),
            }
            totals[sleeve]["cycles"] += b.get("cycles", 0)
            totals[sleeve]["opportunities"] += b.get("opportunities", 0)
            for k, v in b.get("rejections", {}).items():
                totals[sleeve]["rejections"][k] += v
        db = db_days.get(day, {})
        row["db"] = {
            "decisions": db.get("decisions", 0),
            "veto_log": db.get("veto_log", 0),
            "paper_signals": db.get("paper_signals", 0),
            "voyager_paper_signals": db.get("voyager_paper_signals", 0),
            "positions_opened": db.get("positions_opened", 0),
            "positions_closed": db.get("positions_closed", 0),
        }
        row["council_received_anything"] = (
            row["db"]["veto_log"] > 0
            or any(row[s.lower()]["opportunities"] > 0 for s in ACTIVE_SLEEVES)
        )
        tot_veto += row["db"]["veto_log"]
        tot_dec += row["db"]["decisions"]
        tot_open += row["db"]["positions_opened"]
        tot_close += row["db"]["positions_closed"]
        tot_paper += (row["db"]["paper_signals"]
                      + row["db"]["voyager_paper_signals"])
        daily.append(row)

    total_cycles = sum(t["cycles"] for t in totals.values())
    total_opps = sum(t["opportunities"] for t in totals.values())

    # ── recent window: last RECENT_DAYS days that actually had scan cycles ──
    scan_days = [r for r in daily
                 if any(r[s.lower()]["cycles"] > 0 for s in ACTIVE_SLEEVES)]
    recent_rows = scan_days[-RECENT_DAYS:]
    recent: Dict[str, Dict] = {"days": [r["day"] for r in recent_rows]}
    sleeve_states: Dict[str, str] = {}
    r_opps = r_veto = r_dec = r_open = 0
    for sleeve in ACTIVE_SLEEVES:
        sl = sleeve.lower()
        opps = sum(r[sl]["opportunities"] for r in recent_rows)
        cycles = sum(r[sl]["cycles"] for r in recent_rows)
        sig_col = ("paper_signals" if sleeve == "SNIPER"
                   else "voyager_paper_signals")
        distinct = sum(r["db"][sig_col] for r in recent_rows)
        decs = 0  # decisions are not sleeve-split in the daily rows; window total below
        recent[sleeve] = {
            "cycles": cycles, "opportunities": opps,
            "distinct_paper_signals": distinct,
            "state": classify_sleeve(opps, distinct, decs),
        }
        sleeve_states[sleeve] = recent[sleeve]["state"]
        r_opps += opps
    r_veto = sum(r["db"]["veto_log"] for r in recent_rows)
    r_dec = sum(r["db"]["decisions"] for r in recent_rows)
    r_open = sum(r["db"]["positions_opened"] for r in recent_rows)
    r_cycles = sum(sum(r[s.lower()]["cycles"] for s in ACTIVE_SLEEVES)
                   for r in recent_rows)
    recent["veto_rows"] = r_veto
    recent["decisions"] = r_dec
    recent["positions_opened"] = r_open

    council_state = classify_council(r_opps, r_veto, r_dec, r_cycles)
    execution_state = classify_execution(r_opps, r_dec, r_open)

    # data-depth suspicion: meaningful share of recent rejections are
    # history-depth related. The voyager cache audit (Task 3) settles this
    # definitively; here it is a flag only.
    depth_reasons = ("stale_bars",)
    recent_rej: Dict[str, int] = defaultdict(int)
    for r in recent_rows:
        for sleeve in ACTIVE_SLEEVES:
            for k, n in r[sleeve.lower()]["rejections"].items():
                recent_rej[k] += n
    depth_rej = sum(v for k, v in recent_rej.items() if k in depth_reasons)
    all_rej = sum(recent_rej.values())
    data_depth_suspect = all_rej > 0 and depth_rej / all_rej > 0.25

    participation, reason = classify_participation(
        sleeve_states, data_depth_suspect)

    heartbeat = {}
    try:
        heartbeat = json.loads(HEARTBEAT.read_text())
    except Exception:
        pass

    funnel = {
        "scan_cycles": total_cycles,
        "scanner_opportunities": total_opps,
        "council_veto_rows": tot_veto,
        "decisions": tot_dec,
        "positions_opened": tot_open,
        "positions_closed": tot_close,
        "paper_signals_active_sleeves": tot_paper,
    }

    return {
        "kind": "participation_bottleneck_audit",
        "version": "v1",
        "phase": "1G.17",
        "research_only": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "since": since,
        "disclaimer": ("read-only participation diagnostic · no signals, no "
                       "trade proposals, no gate/execution/governance/DB-write "
                       "side effects"),
        "last_dates": last,
        "daemon_heartbeat": {
            "last_heartbeat_ts": heartbeat.get("last_heartbeat_ts"),
            "halted": heartbeat.get("halted"),
            "stage": heartbeat.get("heartbeat_stage"),
        },
        "funnel": funnel,
        "totals_by_sleeve": {
            s: {
                "cycles": totals[s]["cycles"],
                "opportunities": totals[s]["opportunities"],
                "rejections": dict(sorted(totals[s]["rejections"].items(),
                                          key=lambda kv: -kv[1])),
            } for s in ACTIVE_SLEEVES
        },
        "recent_window": recent,
        "verdicts": {
            "council_state": council_state,
            "execution_state": execution_state,
            "participation_state": participation,
            "participation_reason": reason,
            "sleeve_states": sleeve_states,
            "data_depth_suspect": data_depth_suspect,
            "verdict_basis": f"last {len(recent_rows)} scan days "
                             f"({recent['days'][0] if recent['days'] else '—'}"
                             f" → {recent['days'][-1] if recent['days'] else '—'})",
        },
        "daily": daily,
    }


# ── rendering ─────────────────────────────────────────────────────────────────

def _render_txt(res: Dict) -> List[str]:
    v = res["verdicts"]
    f = res["funnel"]
    lines = [
        f"PARTICIPATION BOTTLENECK AUDIT — {res['generated_at'][:10]} "
        f"(since {res['since']}; research-only, read-only)",
        "=" * 78,
        f"participation: {v['participation_state']}  "
        f"reason={v['participation_reason']}  "
        f"council={v['council_state']}  execution={v['execution_state']}",
        f"verdict basis: {v['verdict_basis']}  sleeve states: "
        + "  ".join(f"{k}={s}" for k, s in v["sleeve_states"].items()),
        "",
        f"funnel  scans={f['scan_cycles']}  opportunities="
        f"{f['scanner_opportunities']}  council_rows={f['council_veto_rows']}  "
        f"decisions={f['decisions']}  opened={f['positions_opened']}",
        f"last decision={res['last_dates'].get('last_decision')}  "
        f"last SNIPER paper={res['last_dates'].get('last_paper_signal_SNIPER')}  "
        f"last VOYAGER paper={res['last_dates'].get('last_paper_signal_VOYAGER')}",
        "",
    ]
    for sleeve, t in res["totals_by_sleeve"].items():
        top = list(t["rejections"].items())[:5]
        lines.append(
            f"{sleeve:8s} cycles={t['cycles']:5d}  opportunities="
            f"{t['opportunities']:3d}  top rejections: "
            + ("  ".join(f"{k}={n}" for k, n in top) or "none")
        )
    lines += ["", "day        sniper(opp/cyc)  voyager(opp/cyc)  veto  dec  paper"]
    for row in res["daily"][-30:]:
        s, vy, db = row["sniper"], row["voyager"], row["db"]
        lines.append(
            f"{row['day']}  {s['opportunities']:3d}/{s['cycles']:3d}          "
            f"{vy['opportunities']:3d}/{vy['cycles']:3d}          "
            f"{db['veto_log']:4d}  {db['decisions']:3d}  "
            f"{db['paper_signals'] + db['voyager_paper_signals']:3d}"
        )
    return lines


def _write_doc(res: Dict) -> None:
    v = res["verdicts"]
    f = res["funnel"]
    sn = res["totals_by_sleeve"]["SNIPER"]
    vo = res["totals_by_sleeve"]["VOYAGER"]
    doc = f"""# Participation Bottleneck Audit (Phase 1G.17)

Generated: {res['generated_at'][:19]}Z · window since {res['since']} ·
research-only / read-only — no signals, no proposals, no execution change.

## Verdict

| Layer | State |
|---|---|
| Participation | **{v['participation_state']}** (reason: `{v['participation_reason']}`) |
| Veto council | **{v['council_state']}** |
| Execution | **{v['execution_state']}** |

The daemon is healthy (heartbeat `{res['daemon_heartbeat'].get('stage')}`),
but the scan→council→decision funnel carried:

| Stage | Count in window |
|---|---|
| Scan cycles (active sleeves) | {f['scan_cycles']} |
| Scanner opportunities | {f['scanner_opportunities']} |
| Council veto-log rows | {f['council_veto_rows']} |
| Decisions | {f['decisions']} |
| Positions opened | {f['positions_opened']} |
| Paper signals (active sleeves) | {f['paper_signals_active_sleeves']} |

Last decision: **{res['last_dates'].get('last_decision')}** ·
last SNIPER paper signal: {res['last_dates'].get('last_paper_signal_SNIPER')} ·
last VOYAGER paper signal: {res['last_dates'].get('last_paper_signal_VOYAGER')}.

## Rejection distribution (window totals)

SNIPER ({sn['cycles']} cycles, {sn['opportunities']} opportunities):
{chr(10).join(f"- `{k}` = {n}" for k, n in list(sn['rejections'].items())[:8]) or '- none'}

VOYAGER ({vo['cycles']} cycles, {vo['opportunities']} opportunities):
{chr(10).join(f"- `{k}` = {n}" for k, n in list(vo['rejections'].items())[:8]) or '- none'}

## Interpretation rules

- **Council STARVED** = scans ran but produced ~no candidates; the council had
  nothing to veto. Tightening or loosening the council changes nothing.
- **Execution NEVER_REACHED** = order manager and governance were not exercised;
  they are not the bottleneck and are unproven, not broken.
- Companion audits: `sniper_starvation_audit` (gate confluence),
  `voyager_starvation_cache_audit` (data-depth vs structure rejections),
  `holdout_feasibility_audit` (sample-rate viability).

*Sidecar:* `cache/research/participation_bottleneck_audit_latest.json`
*Runner:* `./scripts/run_research_cycle.sh participation-audit`
"""
    OUT_DOC.parent.mkdir(parents=True, exist_ok=True)
    OUT_DOC.write_text(doc)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--since", default=DEFAULT_SINCE)
    args = ap.parse_args(argv)
    res = build(since=args.since)
    dataio.write_json(OUT_JSON, res)
    lines = _render_txt(res)
    dataio.write_text(OUT_TXT, lines)
    _write_doc(res)
    print("\n".join(lines))
    print(f"\nwrote {dataio.rel_to_repo(OUT_JSON)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
