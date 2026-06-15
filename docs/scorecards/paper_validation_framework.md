# Paper Validation Framework

**Status:** Active platform framework  
**Last updated:** 2026-04-22

## Active Paper Sleeve Set

Frozen for this phase:

| Sleeve | Paper status | Baseline tag | Notes |
|---|---|---|---|
| VOYAGER | Active paper sleeve | `VOYAGER_PAPER` | Long-horizon institutional accumulation / leadership sleeve. Do not redesign in this phase. |
| SNIPER | Active paper sleeve | `SNIPER_V6` | Canonical v6 baseline. Do not tune further unless paper evidence reveals a new doctrine-level issue. |
| SHORT Sleeve A | Active paper candidate | `SHORT_A` | Event-driven tactical short. Monitor in paper; Sleeve B remains research-only. |
| REMORA | Inactive / research-only | `REMORA_RESEARCH_ONLY` | Closed for this phase. |
| CONTRARIAN | Inactive / research-only | `CONTRARIAN_RESEARCH_ONLY` | Closed for this phase; key lesson is stand down in extreme panic. |
| SHORT Sleeve B | Inactive / research-only | `SHORT_B_RESEARCH_ONLY` | Not part of paper engine. |

The paper engine should track only VOYAGER, SNIPER v6, and SHORT Sleeve A during
this phase.

Operational source of truth:

- `core/strategy_registry.py`
- `docs/strategy/STRATEGY_REGISTRY.md`

The registry is consumed by runtime scanning, paper governance, paper ledger
writes, tactical outcome resolution, the unified scoreboard, and dashboard
active views. Frozen sleeves are allowed to appear only in a separate
research-only/frozen status section.

## Existing Infrastructure

Already existed:

- `core/voyager_paper_logger.py`
  - Voyager-specific signal table: `voyager_paper_signals`
  - Captures Voyager entry, archetype, 13F overlay fields, MA data, regime
    context, and 30d/90d/180d outcome fields.
- `research/paper_trades/voyager_paper_report.py`
  - Voyager-specific paper report and outcome updater.
- `core/decision_logger.py`
  - Generic execution/veto logging, but not a full paper-validation ledger.

Added for the unified paper phase:

- `core/paper_validation.py`
  - Generic `paper_signals` table for SNIPER v6, SHORT Sleeve A, and future
    active paper sleeves.
  - Rejects paper ledger writes for non-active sleeves according to
    `core/strategy_registry.py`.
  - Generic `paper_signal_outcomes` table for horizon-specific results.
  - Public helpers:
    - `log_paper_signal(...)`
    - `record_paper_outcome(...)`
    - `mark_paper_signal_closed(...)`
    - `fetch_paper_signals(...)`
- `research/paper_trades/paper_scoreboard.py`
  - Combined paper scoreboard reading both generic paper tables and existing
    Voyager paper table.
- `research/paper_trades/resolve_tactical_outcomes.py`
  - Automated outcome resolver for tactical paper sleeves.
  - Reads `paper_signals`, pulls cache-first Alpaca daily bars, and upserts
    horizon rows into `paper_signal_outcomes`.
- `scripts/run_paper_evidence.py`
  - Daily ops wrapper for paper evidence.
  - Runs tactical outcome resolution first, then refreshes the unified
    scoreboard report.
  - Writes `logs/paper_evidence_status.json` and
    `logs/paper_scoreboard_latest.txt`.
- `systemd/gem-trader-paper-evidence.service`
- `systemd/gem-trader-paper-evidence.timer`
  - Runs the wrapper once per weekday at 18:15 America/New_York.
- `main.py`
  - Runtime wiring for scanner-qualified `SNIPER_V6` and `SHORT_A` candidates.
  - Runtime scanner loop now instantiates active paper scanners only:
    VOYAGER, SNIPER, and SHORT Sleeve A.
  - Applies paper governance before a candidate is counted as valid open paper
    evidence.
  - Governance-blocked candidates are recorded with status
    `governance_blocked`, not silently dropped.

## Paper Signal Record

Every active paper signal should capture at minimum:

- timestamp
- strategy and sleeve
- ticker
- side
- signal version / baseline tag
- entry price
- stop / target
- risk-reward if available
- regime context
- key features used at entry
- score if available
- sector
- size / allocation bucket if modeled
- notes explaining why the trade qualified

Generic table:

- `paper_signals`

Voyager legacy table:

- `voyager_paper_signals`

## Outcome Record

Outcome tracking should be strategy-aware:

| Sleeve | Primary horizons | Secondary horizons |
|---|---|---|
| SNIPER v6 | 10d | 1d / 3d / 5d |
| SHORT Sleeve A | 3d | 5d / 10d |
| VOYAGER | 30d / 90d / 180d | weekly open-trade review |

For each measured horizon, capture where feasible:

- realized paper return
- adjusted return if friction is modeled
- stop-hit
- target-hit
- MAE
- MFE
- outcome date
- still-open / hold-complete state
- exit path: stop, target, timeout, early invalidation, hold-complete

Generic table:

- `paper_signal_outcomes`

Run tactical outcome resolution:

```bash
SNIPER_ENV_PATH=/home/gem/secure/trading.env .venv/bin/python research/paper_trades/resolve_tactical_outcomes.py
```

The resolver is idempotent: each `(signal_id, horizon_days)` row is upserted.
If a horizon has not matured, the row is marked `still_open`. Once a stop,
target, or maximum tactical horizon resolves, the parent `paper_signals` row is
closed with the final path.

Scheduled daily paper-evidence run:

```bash
SNIPER_ENV_PATH=/home/gem/secure/trading.env .venv/bin/python scripts/run_paper_evidence.py
```

Systemd units:

- `gem-trader-paper-evidence.service`
- `gem-trader-paper-evidence.timer`

Timer:

- `Mon-Fri 18:15 America/New_York`

This time is after the regular US equity session and gives daily bars time to
settle for Alpaca/cache reads. The job is safe to rerun because outcome rows are
upserted by `(signal_id, horizon_days)`.

## Immutable Baseline Tags

Active paper tags are fixed identifiers:

- `VOYAGER_PAPER`
- `SNIPER_V6`
- `SHORT_A`

The paper ledger rejects silent tag drift for active sleeves. If scanner logic
changes later, the new logic must receive a new baseline tag instead of
mutating these tags in place.

## Raw Vs Effective Evidence

Raw paper rows are retained for audit and debugging. They are not destructively
collapsed.

Effective evidence rows are the de-duplicated rows used for governance,
promotion, and scoreboard interpretation. Voyager is the main current example:
historical duplicate open rows remain visible as raw rows, while the scoreboard
collapses repeated open exposure by `(strategy, ticker, side)` for effective
paper evidence.

## Scoreboard

Run:

```bash
SNIPER_ENV_PATH=/home/gem/secure/trading.env .venv/bin/python research/paper_trades/paper_scoreboard.py
```

Scoreboard views:

- active sleeve set
- inactive / research-only sleeve set
- raw vs effective evidence rows
- per-strategy signal count
- open paper trades
- horizon completion counts for SNIPER v6 and SHORT Sleeve A
- primary-horizon win rate
- primary-horizon average paper return
- stop-hit / target-hit where available
- combined portfolio signal count
- ticker and sector concentration
- recent open paper trades
- recent closed paper trades

Dashboard policy:

- active opportunity sections show only `VOYAGER`, `SNIPER_V6`, and `SHORT_A`
- frozen sleeves are not shown as active/tradable opportunities
- frozen sleeves may appear only as frozen/research-only status context

## Dashboard Validation Layer

`dashboards/gem_trader_hq.py` now surfaces paper validation directly in the
operator dashboard.

Monitor mode includes active-paper validation panels:

- **Paper Evidence**
  - active sleeves only: `VOYAGER`, `SNIPER_V6`, `SHORT_A`
  - raw rows
  - effective rows
  - open / closed counts
  - governance-blocked counts
  - observe-only / duplicate counts
  - latest resolver and scoreboard timestamps
- **Paper Readiness**
  - `SNIPER_V6`: progress toward 30 paper signals, 10d evidence when mature
  - `SHORT_A`: progress toward 30 paper signals, 3d evidence when mature
  - `VOYAGER`: progress toward 30 closed 30d windows
  - immature samples are shown as `not enough evidence yet`
- **Governance Blocks**
  - same-ticker blocks
  - sector blocks
  - regime-cluster blocks
  - max-position blocks
  - duplicate/open-exposure blocks
  - frozen-sleeve blocks

Risk mode includes **Evidence Freshness**:

- daily-bar current status
- universe snapshot age
- scanner last cycle age
- paper resolver last success
- scoreboard refresh timestamp
- paper loop health

Research mode includes a separate **Research Assist** panel for discretionary
manual research. It is explicitly not paper evidence and must not be used as a
paper-promotion input.

Research Assist methodology:

- Market Posture research context comes from `core/research_assist_bte.py`
  (file name retained for backward compatibility; the user-facing panel and
  cross-reference markers say "Market Posture" / "POSTURE+").
- Market Posture is unrelated to the legacy Breakout Timing Engine blueprint
  in `docs/strategy/BREAKOUT_TIMING_ENGINE_BLUEPRINT.md`; that blueprint
  describes an unbuilt Sniper-specific breakout-timing overlay and is not
  implemented at this time.
- Market Posture is advisory-only and cache-only. It consumes the current
  universe snapshot, regime context, and VIX; it does not call providers,
  route orders, log paper evidence, or override active sleeves.
- Top liquid ranks `strategy_candidates` by explicit `avg_dollar_volume_20`
  with `current_dollar_volume` as a tie-break.
- Top trending ranks by weighted absolute 5d/20d price movement multiplied by
  `volume_ratio_5d`.
- All Research Assist output inherits the universe snapshot freshness and is for
  discretionary/manual research only.

Voyager-specific report remains:

```bash
SNIPER_ENV_PATH=/home/gem/secure/trading.env .venv/bin/python research/paper_trades/voyager_paper_report.py --update-outcomes
```

Scheduled scoreboard output:

- `logs/paper_scoreboard_latest.txt`

Ops status:

```bash
./scripts/check_status.sh
```

The status script shows whether the latest paper-evidence job succeeded, when it
finished, the last successful and failed run timestamps, how many tactical
signals/outcomes it saw, and the latest report path.

## Daily Ops Run Order

1. Daily bars become current after the regular market close.
2. `gem-trader-paper-evidence.timer` starts
   `gem-trader-paper-evidence.service` at 18:15 America/New_York.
3. `scripts/run_paper_evidence.py` runs tactical outcome resolution.
4. The same wrapper refreshes the unified paper scoreboard.
5. Human review uses `logs/paper_scoreboard_latest.txt` and
   `./scripts/check_status.sh`.

Trustworthy paper evidence means the signal was logged by an active paper sleeve,
passed paper governance, belongs to the immutable baseline tag, and has been
processed by the scheduled evidence loop after its relevant horizon matured.

## Promotion Rules

No sleeve is promoted to capital in this phase.

Research-only to paper requires:

- doctrine-clean identity
- explicit trigger and invalidation
- scanner/council conflict understood
- production-aligned scanner/backtest evidence
- positive adjusted expectancy at the primary horizon
- acceptable stop-hit rate
- enough historical sample to justify forward observation

Paper to capital-ready candidate requires:

| Sleeve | Minimum paper evidence |
|---|---|
| SNIPER v6 | At least 30 paper signals; 10d WR >= 50%; 10d avg adjusted return > 0%; stop-hit < 50%; no single ticker > 30%; controls < 10%. |
| SHORT Sleeve A | At least 30 paper signals; primary 3d adjusted expectancy > 0%; stop-hit rate acceptable versus backtest; closed-short outcomes prove borrow/execution path is workable; no event-timing drift. |
| VOYAGER | At least 30 paper signals with 30d window closed; 30d avg return > 0%; 30d MA200 hold rate >= 70%; 90d WR >= 50% once sample is meaningful; 13F overlay not harmful. |

Cross-sleeve capital promotion requires:

- no major divergence from backtest logic
- no unacceptable sector/ticker concentration
- paper fills and exits behave as expected
- portfolio governance did not block most apparently good signals
- council/profile mismatches are either resolved or shown not to damage the
  sleeve in paper

## Current Blocker

The framework is operational for forward paper evidence. Remaining limitations:

1. Historical duplicate Voyager rows still exist as raw audit data. They are
   collapsed in the effective scoreboard/governance view, but no destructive
   cleanup migration has been run.
2. Tactical outcome resolution now exists as a script. It still needs to be
   enabled operationally via `gem-trader-paper-evidence.timer` on the target
   host if the unit has not already been installed.
