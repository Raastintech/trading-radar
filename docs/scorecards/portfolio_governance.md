# Portfolio Governance For Paper Validation

**Status:** Active paper-governance policy  
**Last updated:** 2026-04-22

## Scope

This policy applies to paper validation only. It does not redesign any strategy,
does not tune scanner thresholds, and does not replace live broker/risk controls.

Active paper sleeves:

- VOYAGER
- SNIPER v6
- SHORT Sleeve A

Inactive / research-only:

- REMORA
- CONTRARIAN
- SHORT Sleeve B
- PATHFINDER

Operational source of truth:

- `core/strategy_registry.py`
- `docs/strategy/STRATEGY_REGISTRY.md`

Paper governance accepts only registry `active_paper` sleeves. Frozen sleeves
are logged/documented as research-only and are not eligible for active paper
evidence in this phase.

## Core Principle

The paper book should measure sleeve quality without allowing one regime, sector,
ticker, or correlated cluster to dominate the evidence.

Rules should be simple, retail-practical, and testable.

## Position Limits

| Rule | Limit |
|---|---:|
| Max concurrent paper positions | 8 |
| Max VOYAGER positions | 3 |
| Max SNIPER positions | 3 |
| Max SHORT Sleeve A positions | 2 |
| Max positions per sector | 2 |
| Max positions per ticker | 1 |
| Max positions in one regime cluster | 4 |

Implementation reference:

- `core/strategy_registry.py`
- `execution/paper_governance.py`
- `main.py` applies this governance helper before `SNIPER_V6` and `SHORT_A`
  scanner-qualified candidates are accepted as open paper evidence.

## Allocation Buckets

Paper allocation is for measurement and comparability, not capital deployment.

| Sleeve | Bucket | Paper allocation reference |
|---|---|---:|
| VOYAGER | `long_horizon_probe` | 2.0% |
| SNIPER v6 | `tactical_probe` | 1.5% |
| SHORT Sleeve A | `short_tactical_probe` | 1.0% |

These reference allocations should be logged with the paper signal. They do not
authorize live capital.

## Same Ticker Across Multiple Sleeves

Default rule: one active paper exposure per ticker.

If multiple sleeves fire on the same ticker:

1. Do not stack exposure automatically.
2. Keep the earliest approved paper signal active.
3. Log the later signal as a duplicate/same-ticker governance block if the
   runtime has governance logging enabled.
4. If a manual reviewer wants both signals tracked, record the second as
   `observe_only` with zero allocation and an explicit note.

Priority if simultaneous:

1. SHORT Sleeve A
2. SNIPER v6
3. VOYAGER

Reason: SHORT A and SNIPER are tactical and time-sensitive; VOYAGER has a longer
validation horizon and does not need duplicate same-name stacking.

## Same Regime Cluster

If multiple sleeves fire in the same regime cluster:

- allow up to 4 total paper positions in that cluster
- after 4, log additional signals as governance-blocked or observe-only
- do not use a large single regime event to overstate paper sample quality

Suggested regime clusters:

- `constructive_trend`
- `risk_on_breakout`
- `earnings_event_short`
- `fear_opportunity`
- `extreme_panic`
- `macro_event_lockout`

## Correlated Exposure

Use sector plus ETF/proxy logic as a practical correlation guard:

- max 2 positions per sector
- do not add a SNIPER semiconductor signal if two semiconductor paper trades are
  already open
- do not add multiple broad-market beta proxies as if they were independent
  stock evidence
- ETFs are controls unless a sleeve explicitly defines them as tradable

## Hostile Regime Reductions

Paper validation should still observe candidates in hostile regimes, but sizing
and interpretation must be explicit:

- `VIX proxy >= 35`: CONTRARIAN is inactive; SNIPER should already be suppressed
  by its regime gate; VOYAGER should not add ordinary full-risk exposure.
- broad macro-event lockout: no new tactical paper entries unless the sleeve
  explicitly permits that regime.
- extreme panic: new entries require sleeve-specific doctrine. Do not improvise.

## Testable Governance Rules

The helper `evaluate_paper_signal(signal, open_paper_positions)` in
`execution/paper_governance.py` enforces:

- active-sleeve only
- max concurrent positions
- max positions per strategy
- max positions per sector
- max same-ticker exposure
- max regime-cluster exposure
- allocation bucket assignment

Runtime behavior:

- approved candidates are inserted into `paper_signals` with `status='open'`
- blocked candidates are inserted with `status='governance_blocked'`
- blocked candidates retain the reason in `notes`
- existing duplicate Voyager rows are collapsed to one effective exposure for
  governance and scoreboard evidence views
- raw paper rows remain available for audit/debug; effective de-duplicated rows
  are used for promotion and concentration evidence

This helper is intentionally separate from live `PortfolioRisk` because the live
risk code has older book assumptions and should not be refactored during this
paper-framework pass.

## Scheduled Evidence Loop

Paper governance only becomes promotion-quality evidence after the scheduled
paper-evidence loop has processed outcomes and refreshed the report.

Systemd units:

- `gem-trader-paper-evidence.service`
- `gem-trader-paper-evidence.timer`

Schedule:

- `Mon-Fri 18:15 America/New_York`

Run order:

1. daily bars current
2. tactical resolver runs for `SNIPER_V6` and `SHORT_A`
3. unified scoreboard refreshes
4. human review reads updated evidence

Operational files:

- `logs/gem-trader-paper-evidence.log`
- `logs/paper_evidence_status.json`
- `logs/paper_scoreboard_latest.txt`

## Baseline Tag Discipline

Governance assumes active paper tags are immutable:

- `VOYAGER_PAPER`
- `SNIPER_V6`
- `SHORT_A`

If a sleeve changes logic later, it must receive a new paper tag. Existing tags
must not be reused for silently mutated scanner logic, because promotion
evidence depends on clean version boundaries.

## Review Cadence

Daily:

- review new signals and governance blocks
- confirm open paper positions still match their thesis

Weekly:

- run the unified paper scoreboard
- run `research/paper_trades/resolve_tactical_outcomes.py` for SNIPER v6 and
  SHORT Sleeve A tactical horizons
- run Voyager-specific outcome updater if needed
- review sector/ticker concentration
- compare live paper behavior against backtest expectations

Daily scheduled automation handles the first two weekly commands through
`scripts/run_paper_evidence.py`; manual runs remain valid because the resolver
is idempotent.

Monthly or after every 30 closed/aged signals:

- evaluate paper promotion gates
- decide whether a sleeve remains paper, becomes capital-ready candidate, or
  returns to research-only
