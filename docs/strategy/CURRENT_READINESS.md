# Current Readiness

> **⚠ HISTORICAL DOCUMENT — NOT CURRENT OPERATIONAL TRUTH**
>
> All trading strategies described below (SNIPER, VOYAGER, SHORT, REMORA, CONTRARIAN)
> are **permanently decommissioned** as of Phase 3A (2026-06-13).
> This document records the pre-decommission trading phase for audit purposes only.
>
> **Current system truth:** `docs/ROADMAP_PHASES.md` → RESEARCH_ONLY mode, permanent.
> For research engine work, ignore everything below. Do not use this file to infer
> sleeve status, paper signal paths, or promotion gates — those paths no longer exist.
>
> Full decommission record: `docs/research/AUTO_TRADING_DECOMMISSION_FINAL_FINDINGS.md`

---

Snapshot Date: `2026-05-04` (pre-decommission)

This document recorded the production readiness state of the trading platform.
It is preserved as historical audit record.

> **Phase 1G.3 update — 2026-05-24 (Evidence Closure + SHORT_A Freeze).**
> Per `docs/strategy/STRATEGY_TRUTH_REVIEW_2026_05.md`:
> - **SHORT_A frozen → research-only** (`core/strategy_registry.py`): no new paper
>   emissions; historical rows preserved and still resolving. Active paper sleeves
>   are now **VOYAGER** and **SNIPER** only.
> - **Short Opportunity Radar** keeps short-side awareness alive (research-only,
>   no signals). Current state: `SHORTS_OFF` (bull tape).
> - **LEADER_RESET** is research-only; event-study verdict **NEED_MORE_DATA**
>   (the entry validator never emits an actionable reclaim — RESEARCH_READY n=0).
>   It does not activate until the event study + validation-plan backtest pass.
> - **VOYAGER** stays active but its approval→signal conversion is ~2.1%; the
>   long-leadership thesis is recommended to fold into LEADER_RESET, not a VOYAGER
>   redesign.
> - **Forward outcomes** are maturing on cadence but young (first maturity
>   2026-05-28). Resolver is healthy.
> - **No live trading, no execution/governance change, no new active strategy,
>   Phase 2C not started.** Doctrine: no active short sleeve is better than a bad
>   one; long-only/cash is an acceptable temporary posture.

> **Phase 12B update — 2026-05-05 (SNIPER forward-OOS instrumentation)**
> Phase 12A's H3 hypothesis cannot be tested on historical SNIPER data
> (cohort collapsed to 1 unique entry). Phase 12B wires the per-signal
> metadata that lets the live cohort accumulate, **without** changing any
> SNIPER threshold, scanner gate, paper-governance rule, execution path,
> sleeve status, dashboard score, Alpha Discovery rule, Daily Entry
> Validator logic, or Market Forecast logic.
>
> **Files changed (additive only):**
> - `core/paper_validation.py` — new additive migration `aux_h3 TEXT` on
>   `paper_signals`; new `compute_h3_metadata()` / `safe_compute_h3_metadata()`
>   helpers (never raise); new optional `aux_h3` parameter on
>   `log_paper_signal()`.
> - `main.py` — `_read_market_forecast_snapshot()` and `_build_sniper_aux_h3()`
>   helpers; `_record_paper_candidate()` attaches `aux_h3` only for
>   `strategy == "SNIPER"`. SHORT and Voyager unaffected.
> - `research/sniper_h3_forward_report.py` — new analysis-only report
>   (zero-row safe, schema-aware) + sidecar JSON.
>
> **Verified end-to-end:** `py_compile` passes on all touched files; the
> additive migration preserves all 1137 existing paper_signals rows with
> `aux_h3=NULL`; existing readers still work; `compute_h3_metadata()`
> handles all-None inputs without raising; the forward report runs cleanly
> on the live DB (current state: zero SNIPER rows) and on a synthetic
> 5-row DB (3 H3 / 2 non-H3 / 1 governance-blocked) with correct cohort
> splits, gate-fail attribution, and missing-metadata counts.
>
> **Per-signal metadata recorded** (SNIPER-only): `sniper_score`, `vix_value`,
> `volume_ratio`, `sector` + canonical, the four buckets used by H3 gates
> (score / VIX / vol-ratio / sector), and **populated-today** auxiliary
> fields `market_forecast_regime`, `market_forecast_bias_5d`,
> `market_forecast_bias_10d`. Other auxiliary fields (DEV state, Market
> Posture bias, options quality, Stock Lens extension, Alpha Discovery
> membership) are recorded as null today; the forward report tracks
> per-field missing counts so coverage is visible without re-reading code.
>
> **H3-candidate tagging.** A signal is `h3_candidate=True` iff all four
> gates pass: score ∈ [80, 90) ∩ VIX ∈ [15, 20) at entry ∩ vol_ratio < 1.5×
> ∩ sector ∈ {Healthcare, Communications, Technology}. Sector aliasing
> handles "Communication Services", "Health Care", "Information Technology",
> and lower-case variants. Missing inputs map to `"missing"` buckets and
> the gate evaluates to False — never to a crash.
>
> **Status banner** (from `research/sniper_h3_forward_report.py`):
> `SNIPER H3 OOS: open X · closed Y · insufficient until 20–30 closed`.
> Dashboard wiring intentionally **deferred** per the user's "do not
> overbuild dashboard UI" guidance — the banner is available from the CLI
> and the JSON sidecar.
>
> **Disposition.** SNIPER stays **paper-only**, **not paused**, **not
> promoted**. Re-evaluate the Phase 12A pass criteria against the live
> cohort once `n_closed ≥ 20–30`.

> **Phase 12A update — 2026-05-04 (SNIPER H3 narrow-cohort historical screen)**
> Phase 11's H3 hypothesis tested as a research-only screen.
> Script: `research/sniper_h3_validation.py` (analysis-only, py_compile OK).
> Sidecar: `docs/scorecards/sniper_h3_validation.json`. Doc updates:
> `docs/research/SLEEVE_FAILURE_AUTOPSY.md` (new "Phase 12A" section) and
> `docs/scorecards/sniper_scorecard.md` (new H3 results block).
>
> **H3 cohort = score [80, 90) ∩ VIX [15, 20) ∩ vol_ratio < 1.5× ∩ sector ∈
> {Healthcare, Communications, Technology}**, evaluated on the 75-unique-entry
> historical SNIPER CSV.
>
> **Result — `INSUFFICIENT_DATA`.** The four gates compose to a near-empty
> cohort: **3 trade-rows = 1 unique entry × 3 horizons** (AVGO 2021-10-26).
> Bootstrap CI on the row-level series overstates information because the
> three rows share an entry-level signal. Even leave-one-gate-out cohorts
> max at 5 unique entries — well below the MIN_N_FOR_CI=30 floor. The
> binding-constraint gate is **vol_ratio < 1.5×**. The point estimate is
> positive (WR 100%, avg adj +3.46%) but n_unique_entries = 1 means the
> hypothesis is **neither confirmed nor refuted**.
>
> **Auxiliary state (DEV / Market Forecast) is not historically available.**
> The historical SNIPER backtest CSV does not snapshot DEV pass/fail or
> Market Forecast regime label at signal-emit time. This gap is recorded
> with a forward-looking join plan: tag forward SNIPER paper signals with
> all four H3 gates plus DEV state and regime label, then re-run the
> validation script after ~6 months of forward accrual.
>
> **Disposition.** SNIPER stays **paper-only**, **not paused**, **not
> promoted**. The H3 result motivates **Phase 12B = live-OOS instrumentation
> only** (no threshold change, no scanner change, no governance change).
>
> No strategy / scoring / scanner / governance / execution / dashboard /
> sleeve-status code was modified in this phase.

> **Phase 11 update — 2026-05-04 (active-sleeve failure autopsy + edge reconstruction plan)**
> Failure-autopsy phase, not a re-tuning phase. Three active sleeves were
> dissected at trade level using the freshly refreshed evidence rigor report
> + a new analysis-only script `research/sleeve_failure_autopsy.py` that joins
> trade-level CSVs, the SPY/QQQ/VIX caches, and notes-derived features
> (score, vol_ratio, borrow_pct, gap_risk_pct, intraday_range_pct).
> Outputs: `docs/scorecards/sleeve_failure_autopsy.json` and
> `docs/research/SLEEVE_FAILURE_AUTOPSY.md`. Per-sleeve scorecards updated
> with an "Autopsy summary" block under the existing rigor strip.
>
> **Headline diagnoses.**
> - `SHORT_A`: 6 of 9 historical trades hit the −10% stop during a +24.3% SPY
>   tape. Two trades opened at VIX ≈ 47 (post-flush). Loser cohort had **3.7×**
>   the intraday range and **4.4×** the gap-risk of the winner cohort. Failure is
>   structural (regime + geometry + leadership shorts), not noise.
> - `VOYAGER_PAPER`: underperforms a same-window SPY benchmark on **every**
>   mandate horizon (−2 to −4pp). 93.6% of cumulative adjusted return comes
>   from 3 names (GE, XOM, COST). The 13F signal as expressed picks
>   already-large already-sponsored names rather than emerging accumulation.
> - `SNIPER_V6`: edge is narrow and saturated. Score 90+ underperforms 80–89;
>   the highest-WR cohort is *low* volume ratio (<1.5×); 2024 is the first
>   negative year. Friction-fragile (edge inverts at 1% RT).
>
> **Dispositions.** All three remain **paper-only**. Nothing paused.
> **VOYAGER and SHORT_A historical require redesign** before further evidence
> is meaningful; SNIPER continues evidence accrual on its surviving cohort.
>
> **Three narrow next-research hypotheses (analysis-only, no live promotion
> until each test passes its falsification criterion):**
> 1. **H1 — SHORT_A:** entries only when normal VIX (15–20) + intraday range
>    < 4% + gap-risk < 1.5 + (optional) prior-5d underlying return < +3%.
> 2. **H2 — VOYAGER:** entries only when sector ETF 60d-RS ≥ 0.6 + 13F-Δ
>    sponsorship > 0 + breadth advancing-pct ≥ 55. Test against SPY
>    same-window, not just universe-restricted random.
> 3. **H3 — SNIPER:** evaluate the cohort intersection of score 80–89 ∩ VIX
>    15–20 ∩ vol-ratio < 1.5× ∩ Healthcare/Communications/Technology, with
>    Daily Entry Validator and Market Forecast state recorded at entry.
>
> No strategy thresholds, scanner logic, sleeve status, paper governance,
> execution, dashboard, Alpha Discovery, Stock Lens, Market Forecast, or
> Daily Entry Validator code was modified.

> **Phase 9B / Phase 10 / Phase 10B update — 2026-05-04 (SHORT_A historical export now run)**
> Evidence rigor pipeline now in place. Trade-level CSVs exported for the three
> active sleeves; bootstrap CIs, walk-forward stability, random-entry control,
> friction sensitivity, and an all-horizons aggregate run on each. See
> `docs/scorecards/evidence_rigor_report.md`. No strategy thresholds, scanner
> logic, paper governance, or execution were modified.
>
> | Sleeve | n closed (agg) | Primary horizon | Aggregate adj (95% CI) | vs random (aggregate) | Verdict |
> |---|---:|---:|---|---|---|
> | `SNIPER_V6`     | 225 | 10d  | +0.58% [-0.04%, +1.22%]   | +0.42pp adj | **Indistinguishable from random** — paper-ready under current evidence, not capital-proven |
> | `VOYAGER_PAPER` |  64 | 252d | +9.22% [+3.70%, +15.44%]  | +2.64pp adj | **Indistinguishable from random** — aggregate edge over universe-matched random is small once horizons are pooled |
> | `SHORT_A`       |  13 | 5d   | **−4.85% [−8.04%, −1.68%]** | −5.71pp adj | **Weak and thin** — historical export (n=9) reveals most signals hit the −10% stop; aggregate underperforms random by 5.71pp |
>
> All three remain paper-only. None are capital-proven. Verdicts will tighten or
> weaken as live paper closes more trades and historical exports rerun.

> **Phase 10B update — 2026-05-04 (evidence hardening follow-up)**
>
> 1. **Voyager friction now applied at export time.** The Voyager backtest
>    reports raw forward returns; the export defaults to 0.30% RT
>    (`VOYAGER_DEFAULT_FRICTION_RT_PCT`) and the audit emits a sensitivity
>    table at 0%, 0.30%, 0.50%, 1.00% RT. At the mandate-aligned 252-day
>    horizon the headline is robust (avg adj 20.53% → 19.53% across that
>    sweep). On Sniper 10d the headline erodes from +0.87% to −0.13% across
>    the same sweep; the strategy is friction-fragile at the assumption
>    boundary.
> 2. **SHORT_A historical export ran successfully on 2026-05-04** via
>    `SNIPER_ENV_PATH=/home/gem/secure/trading.env .venv/bin/python
>    research/sleeves/export_short_a_history.py`. The backtester returned
>    9 historical trades over the default 504-day lookback against a 200-name
>    discovery universe. Merged with 6 live-paper rows → 15 total / 13 closed.
>    The all-horizons aggregate is **avg adj −4.85% [−8.04%, −1.68%]**,
>    WR 30.8%, with the strategy underperforming an apples-to-apples random
>    control (random adj +0.86%, WR 49.2%) by **−5.71pp adj**. **Verdict
>    downgraded to WEAK_AND_THIN.** The signal: most short trades are hitting
>    the configured −10% stop; the few that don't hit stop don't yet pay for
>    the stops that do. This is honest negative evidence — exactly the
>    purpose of running the historical export.
> 3. **Per-sleeve Phase 10B constraints**
>    - `SNIPER_V6` — paper-only; currently indistinguishable from random at
>      the lower bound. Edge erodes completely if RT friction widens to 1%.
>      Promotion gate: live paper accumulates ≥30 closed trades and a robust
>      verdict from the audit.
>    - `VOYAGER_PAPER` — promising but statistically thin. Friction
>      sensitivity *is* required and now satisfied; the headline survives
>      0–1% RT at the 252d horizon. Promotion gate: more closed signals
>      (n=16 → ≥30 per horizon) plus a second walk-forward window once the
>      data span allows.
>    - `SHORT_A` — **weak and thin** post-historical-export. The aggregate
>      CI is fully negative ([−8.04%, −1.68%]) at n=13, so the verdict no
>      longer reads "promising." Mandate language should remain unchanged
>      until n grows enough to either confirm or refute the negative read
>      (need ≥30 closed); but capital promotion is clearly off the table
>      until the strategy can survive a friction-aware aggregate. Do NOT
>      retune in this phase — the next move is to keep collecting live
>      paper, optionally re-run the historical export with different
>      friction assumptions, and let the audit speak.
> 4. **Guardrails reaffirmed.** No strategy retuning, no scanner changes,
>    no execution changes, no paper governance changes, no capital
>    promotion in this phase.


If this file conflicts with older status language in other docs, this file wins
for current-state assessment. `PROJECT_NORTH_STAR.md` still defines mission, and
`MASTER_PLAN.md` still defines roadmap. `STRATEGY_DOCTRINE.md` defines the
mandate and edge claim each strategy must satisfy before capital promotion.

Current doctrine map:

- `docs/strategy/CURRENT_DOCTRINE_MAP.md` = doctrine/source ordering
- `docs/strategy/CURRENT_READINESS.md` = current platform truth
- `docs/strategy/STRATEGY_DOCTRINE.md` = permanent research framework
- older audit / master / pipeline docs with archival warnings = historical context only

## Active Paper Sleeve Set

Frozen for the current paper-validation phase:

| Sleeve | Status | Baseline |
|---|---|---|
| `VOYAGER` | Active paper sleeve | `VOYAGER_PAPER` |
| `SNIPER` | Active paper sleeve | canonical `SNIPER_V6` |
| `SHORT` | Active paper candidate | `SHORT_A` |
| `REMORA` | Inactive / research-only | closed for this phase |
| `CONTRARIAN` | Inactive / research-only | closed for this phase |
| `SHORT_B` | Inactive / research-only | design/research only |

Current platform direction: stop single-strategy tuning and collect disciplined
paper evidence for VOYAGER, SNIPER v6, and SHORT Sleeve A under unified paper
tracking and paper-governance rules.

## Future Research Sleeves

| Sleeve | Status | Baseline |
|---|---|---|
| `PATHFINDER` | Future research-only, paused | `PATHFINDER_V1` |

PATHFINDER was added as a future research sleeve scaffold on 2026-04-22. It is
not part of the active paper set and is not wired into runtime paper evidence.
Final verification on 2026-04-22 added a local PIT fundamentals proxy and
compared tape-only versus business-inflection-filtered backtests. The PIT layer
improved 20d outcomes but collapsed the sample to 3 trades over 2020-2024, with
heavy AXON concentration and Voyager-like technical structure. Verdict:
`PAUSE / SHELVE PATHFINDER FOR NOW`; do not tune or wire it into paper/live in
this phase.

## Research Discovery Layer

`ALPHA_DISCOVERY_BOARD` was added as a research-only discovery subsystem on
2026-04-24 and calibrated to V2 in the same phase.

Purpose:

- early long-side opportunity discovery
- buyable pullback / sponsor-confirmation review
- manual research triage before sleeve approval

Important boundaries:

- not a sleeve
- not paper evidence
- not paper governance input
- not auto-tradable
- not active paper output

Primary code / report paths:

- `core/alpha_discovery.py`
- `research/alpha_discovery_board.py`
- `docs/scorecards/alpha_discovery_board.md`

Data use:

- Alpaca/local universe snapshot for tape / liquidity / participation
- FMP for market cap / sector / fundamentals enrichment
- 13F as optional slow sponsorship overlay only
- Tradier as optional low-weight options-participation overlay only
- nightly Alpha Discovery now runs a cache-first enrichment prewarm so canonical builds are less dependent on thin ad hoc FMP coverage

The board is intended for morning manual review and stalking, not for silent
promotion into `VOYAGER`, `SNIPER_V6`, or `SHORT_A`.

V2 calibration outcome:

- V1 was too dominated by already-obvious liquid leaders
- V2 now separates two explicit tracks:
  - `Liquid Leadership Reset`
  - `Emerging Opportunity`
- this remains a research/discovery layer only
- it is still not a sleeve and not paper evidence
- V2.1 tightened daily-chart actionability truthfulness:
  - `Buyable Now` is materially stricter
  - `entry still forming` no longer counts as fully actionable
  - more names are honestly labeled as pullback-forming / watch-reclaim / watch-only
  - a separate `DAILY_ENTRY_VALIDATOR` module now sits on top of Alpha Discovery
    so discovery and daily-entry truthfulness remain separate roles
- nightly build is the canonical Alpha Discovery artifact
- premarket overlay is a lighter actionability refinement pass
- Research mode now includes a dedicated Alpha Discovery dashboard panel for:
  - `Buyable Now`
  - `Pullback Watch`
  - `Early Discovery`
  - `Too Late / Crowded`
- this visual board remains discretionary/manual research only and must not be
  confused with paper evidence, sleeve approval, or trade approval

Paper framework references:

- `core/strategy_registry.py`
- `docs/strategy/STRATEGY_REGISTRY.md`
- `docs/scorecards/paper_validation_framework.md`
- `docs/scorecards/portfolio_governance.md`
- `core/paper_validation.py`
- `execution/paper_governance.py`
- `research/paper_trades/paper_scoreboard.py`
- `research/paper_trades/resolve_tactical_outcomes.py`
- `scripts/run_paper_evidence.py`
- `systemd/gem-trader-paper-evidence.service`
- `systemd/gem-trader-paper-evidence.timer`
- `docs/strategy/PATHFINDER_EARLY_SPONSORSHIP_SPEC.md`
- `docs/scorecards/pathfinder_scorecard.md`

Operational enforcement:

- `main.py` scans only active paper scanners from `core/strategy_registry.py`:
  `VOYAGER`, `SNIPER`, and `SHORT_A`.
- `dashboards/gem_trader_hq.py` filters active opportunity/candidate views to
  active paper sleeves only and shows frozen sleeves only in a separate frozen
  status note.
- `dashboards/gem_trader_hq.py` now has a validation-first dashboard layer:
  Paper Evidence, Paper Readiness, Governance Blocks, and Evidence Freshness.
  These panels read the paper DB/status files and report active paper sleeves
  only.
- Research mode has a separate Research Assist panel for discretionary/manual
  research. It shows cache-only Market Posture market-direction context
  (formerly labelled "BTE" in the dashboard, unrelated to the legacy Breakout
  Timing Engine blueprint) plus top liquid/trending lists and explicitly
  labels them as not paper evidence.
  Top liquid is based on `avg_dollar_volume_20` with latest completed-bar
  `current_dollar_volume` as a tie-break. Top trending is based on weighted
  5d/20d price movement multiplied by `volume_ratio_5d`.
- Research Assist is operationally separate from paper evidence: it does not
  log paper rows, affect governance, promote sleeves, or turn frozen sleeves
  into tradable suggestions.
- `core/paper_validation.py`, `execution/paper_governance.py`,
  `research/paper_trades/resolve_tactical_outcomes.py`, and
  `research/paper_trades/paper_scoreboard.py` all consume the same registry so
  frozen sleeves do not generate active paper evidence.

## Purpose

This project aims to become a world-class retail trading intelligence system.
That is the ambition.

It is not honest to call the system "complete", "A+", or "production-ready"
without separating:

1. runtime and safety
2. telemetry and attribution
3. strategy quality and calibration
4. capital-promotion readiness

Those are different gates. Passing one does not imply the others are done.


## Readiness Model

### Layer 1: Runtime And Safety

Definition:
- canonical daemon path is stable
- startup and shutdown are controlled
- tests pass
- heartbeat, logs, and DB writes are working
- fail-closed readiness checks exist

Current status:
- `GREEN`

Current evidence:
- canonical runtime is `start_trader.sh -> unified_master_trader_v3.py`
- startup readiness gate is active
- daemon heartbeat and logs are active
- latest full test run: `259 passed, 1 warning`


### Layer 2: Telemetry And Attribution

Definition:
- decision -> trade -> outcome path is observable
- scanner rejects are attributable by strategy and gate
- shadow/pilot outcomes resolve correctly
- analytics exclude known invalid test trades cleanly
- no major blind spots in forward decision logging

Current status:
- `AMBER`

Current evidence:
- unified paper-validation framework added 2026-04-22:
  - active paper set frozen as `VOYAGER`, `SNIPER_V6`, and `SHORT_A`
  - `REMORA`, `CONTRARIAN`, and `SHORT_B` are explicitly inactive/research-only
  - generic paper ledger added in `core/paper_validation.py`
  - paper-governance helper added in `execution/paper_governance.py`
  - combined paper scoreboard added in `research/paper_trades/paper_scoreboard.py`
  - tactical paper outcome resolver added in
    `research/paper_trades/resolve_tactical_outcomes.py`
  - daily paper-evidence wrapper added in `scripts/run_paper_evidence.py`
  - systemd paper-evidence unit/timer added for `Mon-Fri 18:15
    America/New_York`; run order is tactical resolver first, scoreboard second
  - `scripts/check_status.sh` now reports latest paper-evidence success/failure,
    timestamps, signal/outcome counts, and the latest scoreboard path
  - runtime wiring added in `main.py` so `SNIPER_V6` and `SHORT_A`
    scanner-qualified candidates are logged to `paper_signals` after paper
    governance is applied
  - governance-blocked paper candidates are logged with
    `status='governance_blocked'` instead of disappearing silently
  - Voyager's existing paper table/report remain valid and are read by the
    combined scoreboard
  - active paper tags are immutable identifiers: `VOYAGER_PAPER`, `SNIPER_V6`,
    and `SHORT_A`; new strategy logic must receive a new tag
  - the scoreboard preserves raw vs effective paper rows, so historical Voyager
    duplicates remain auditable while effective evidence is de-duplicated for
    governance and promotion review
  - forward same-ticker Voyager de-duplication was added to
    `core/voyager_paper_logger.py` on 2026-04-22
- trustworthy paper evidence now requires an active paper sleeve, immutable
  baseline tag, paper-governance acceptance, and successful daily evidence-loop
  processing after the relevant horizon matures
- PATHFINDER is documented as a future research-only paused sleeve. It must not
  be treated as active paper evidence. The final verification pass added a
  cache-first PIT fundamentals proxy, but the resulting evidence was too sparse
  for continued work in this phase.
- frozen sleeves are now enforced through `core/strategy_registry.py`; REMORA,
  CONTRARIAN, SHORT_B, and PATHFINDER should not appear in active dashboard
  opportunity views or active paper-governance evidence.
- decision logging, trade logging, and outcome resolution are working
- shadow outcomes now resolve via `run_shadow_outcomes` with 1,315 rows (March 18 cohort visible) so the resolver can feed clean evidence back into threshold decisions
- shadow outcome tracking is now multi-horizon: new candidates are seeded at
  both `5d` and `20d`, while dashboards and reports continue to default to
  `5d` so existing operating semantics do not silently change
- invalid test trade `DIS` is excluded from analytics and reconciled closed
- forward Voyager reject telemetry was corrected today so future reject rows can
  retain `rr`, `stop_loss`, and `target_price`
- short reject telemetry now carries both adaptive geometry context and
  sector-relative weakness context, so SHORT failures can be audited from real
  benchmark data rather than placeholders
- trade entry forecasting is now shadow-aware: entry snapshots prefer resolved
  shadow outcomes by policy/regime/horizon and fall back to the legacy
  closed-trade forecaster only when shadow evidence is too thin
- additive ranking and forecast-audit tooling now exists:
  - `best_idea_ranker.py` ranks approved and near-miss candidates using live
    RR thresholds, regime fit, feature context, and shadow/forecast priors
  - `candidate_feature_snapshot.py` persists ranked candidate evidence into
    `candidate_feature_snapshots`
  - `forecast_validation_report.py` exposes forecast coverage and calibration
    truthfully
- weighted-score integrity is now materially cleaner:
  - synthetic `news` / `insider` / `analyst` / placeholder event-risk checks
    now carry zero score weight until real feeds exist
  - live `SNIPER` no longer awards score from proxy institutional ownership,
    proxy short interest, or fake catalyst flags
  - live `VOYAGER` long scoring now feeds the current growth-first scoring
    schema with real fundamentals instead of stale field names
  - premarket and EOD side scanners no longer manufacture weighted inputs by
    flooring `risk_reward` up to threshold or by awarding fake news /
    options / earnings / insider-style flags
  - the shared mapper and fundamental fetchers now fail closed on missing
    quality, short-interest, and earnings data instead of defaulting to
    optimistic `True`
  - `portfolio_manager.py` no longer uses the fake `1% per position` heat
    proxy; portfolio heat now prefers tracked open-trade stop risk from the DB
    and makes any conservative fallback estimate explicit
  - portfolio operator reporting is now direction-aware: sector exposure shows
    gross / long / short / net instead of flattening long and short books into
    a single concentration number
  - portfolio operator reporting is now also strategy-aware: equity strategy
    exposure is reported on broker market value, while options strategy
    exposure is reported separately on defined-risk / max-risk basis so the
    desk view does not mix incomparable exposure types into a fake aggregate
  - portfolio risk state is now refreshed from realized closed trades in the
    canonical `trades` table, so the coordinator's daily / weekly / monthly
    P&L, circuit-breaker state, and heartbeat telemetry are no longer stuck at
    zero after live losses
  - V3 now fail-closes on same-ticker add-ons: because execution and tracking
    are ticker-scoped rather than lot-scoped, duplicate entries in an already
    open ticker are blocked instead of allowing broker exposure to outrun the
    canonical trade log
  - the portfolio coordinator now syncs from the broker book with DB trade
    metadata layered on top, so stop-risk checks are no longer limited to the
    current daemon session's in-memory `active_positions`
  - live operator messaging now distinguishes gross deployed exposure from
    stop-risk heat instead of calling both concepts "portfolio heat"
  - live `REMORA` institutional participation now comes from real live-feed
    block-trade summaries and fails closed when no qualifying tape exists;
    EOD research no longer awards that score weight from a volume proxy
  - `audit_weighted_score_integrity.py` now reports integrity by actual score
    weight, not by raw field count
  - `sentiment_analyzer_v2.py` now uses env-only provider keys; the legacy
    research sentiment path fails closed when keys are absent instead of
    enabling itself with embedded credentials

Remaining gaps:
- historical Voyager reject rows are incomplete and should not be treated as
  canonical for level-based analysis
- live short exit accounting is correct in code but not yet proven by a real
  closed short trade
- legacy imported rows remain non-canonical for strategy inference
- forecast coverage is still sparse and poorly calibrated, so ranking output is
  operator guidance, not autonomous capital logic
- `20d` shadow cohorts are seeded but not yet mature enough to contribute
  resolved outcome evidence


### Layer 3: Strategy Quality

Definition:
- each strategy produces enough clean forward observations to judge quality
- approvals are not structurally suppressed by bad plumbing or dead gates
- threshold changes are supported by forward evidence, not guesswork

Current status:
- `RED / AMBER`

Latest operating snapshot:
- daemon report, last 1 day:
  - scanned: `1204`
  - approved: `0`
  - rejected: `1204`
- dominant reject reasons:
  - `SNIPER`: `risk_reward_too_low`
  - `SHORT`: `risk_reward_too_low`
  - `VOYAGER`: `rs_declining`, `no_voyager_pathway_qualified`,
    `no_accumulation`, `below_50ma`

Strategy status:
- `VOYAGER`: `AMBER`
  - architecture is live
  - Quant Research Doctrine audit completed 2026-04-21:
    - mandate, edge source, trigger archetypes, invalidation, regime fit,
      data integrity, execution realism, and output expectations pass
    - scanner/council fit is partial because the VOYAGER-specific council
      profile is documented but live `VetoCouncil` still uses generic weights
    - recommendation: keep VOYAGER mostly as-is; do not redesign scanner or add
      indicators; run one narrow paper-readiness measurement comparing
      scanner-only vs scanner+council outcomes
    - remaining blocker is paper validation, not doctrine: collect 30 signals
      with at least 30-day outcome tracking before capital review
  - local verification 2026-04-21:
    - Voyager smoke tests passed
    - Mode A full backtest passed after fixing date normalization in
      `research/backtests/voyager_v2_backtest.py`
    - latest Mode A full run: 23 signals, 90d WR 73.9%, 180d avg return +18.1%
    - Mode B / 13F was not rerun in this test pass
  - regime-compatibility logic was corrected forward
  - in `VIX 30-35`, Voyager longs now route through a crisis-accumulation
    sleeve instead of normal full-risk execution:
    - only large-cap (`market_cap >= $10B`), positive-FCF, manageable-debt
      names with real accumulation / stabilization survive
    - accepted names are reduced to a smaller first tranche before ranking
      and execution
    - crash protocol above `VIX >= 35` still suspends all new entries
  - still needs fresh forward evidence after the fix
- `SNIPER`: `AMBER`
  - Quant Research Doctrine verification completed 2026-04-21:
    - paper-ready, not capital-ready
    - mandate, trigger, invalidation, regime fit, data integrity, execution
      realism, and output expectations pass for paper validation
    - scanner/council fit is partial because the scanner is v6-aligned but the
      live council still uses generic weights; this is not a paper-phase blocker
    - local verification passed: 4 SNIPER smoke tests; full 2020-2024 v6
      backtest cleared thresholds with n=75, WR 50.7%, avgAdj +0.57%,
      stop-hit 42.7%
  - six-pass backtest (v1–v6) completed 2026-04-20; v6 configuration clears all thresholds: WR 50.7%, avgAdj +0.57%, stop-hit 42.7%, 4 of 5 years positive
  - production scanner updated to v6 configuration 2026-04-21: ATR contraction gate, MA50 slope gate, SPY 200d MA bear-market gate, 1.5×ATR stop, large-cap universe, `BARS_NEEDED=75`
  - now in paper phase — accumulating forward signals against pre-stated paper-phase criteria (≥ 30 signals, WR ≥ 50%, avgAdj > 0%)
  - pre-update: shadow outcomes (sniper_2_0 / sniper_2_2) showed negative average return (~-2.4%) and high stop rates; those cohorts were generated by the broken scanner (pre-v6) and are not valid evidence for the updated configuration
- `SHORT`: `AMBER`
  - live RR gate is now `3.0`, aligned across scanner, scoring, and diagnostics
  - live score floor is now `55`, lowered from `60` because repeated recent
    rejects were concentrated in the `55-59` band with strong RR and favorable
    short shadow outcomes
  - live stop/target geometry is now adaptive, using ATR + recent swing-high
    caps instead of the old blunt fixed 6% stop frame; this change is grounded
    in the stronger `short_tight_3_0` shadow cohort and is now wired into the
    actual short scanner
  - live relative-strength and distribution logic now uses sector-relative
    excess return, repeated high-volume down days, and benchmark weakness
    rather than placeholder values
  - shadow results remain more promising than Sniper (short_2_5 and short_3_0 averages ~+0.9–1.4% per 3-day sample)
  - March 18 `short_tight_3_0` cohort (81 candidates) averaged +3.29% with few stops, so the next calibration step is `3.0 -> 2.5` in paper/pilot only
  - best-idea ranking is now wired into live execution ordering, so approved
    short candidates are no longer sent to execution in raw scanner order
  - not yet promoted live beyond this threshold change; still needs real
    closed-short proof and pilot validation
- `REMORA`: `RED / AMBER`
  - integrated in V3
  - Quant Research Doctrine identity review completed 2026-04-21:
    - official identity is stealth institutional accumulation / quiet flow
    - REMORA is not a catalyst/news/volume-burst scanner
    - catalyst burst logic should become a separate future sleeve if pursued
    - doctrine / mandate clarity now passes
  - scanner audit completed 2026-04-21:
    - implementation matches quiet-flow identity: quiet price, moderate abnormal
      volume, near-high context, institutional liquidity, short-horizon LONG
    - no catalyst/news burst, breakout confirmation, or panic reversal logic found
    - rejection attribution added to `strategies/remora.py`
    - stale 52-week-high issue resolved by requiring full 252 bars before evaluation
    - missing quote fallback removed; scanner now rejects `no_quote` instead of
      assuming acceptable spread
    - smoke tests passed after patch
    - scanner/council fit remains partial because live `VetoCouncil` uses generic
      weights and intraday flow scoring, while REMORA's thesis is daily volume
      anomaly; workable for validation if scanner-only and scanner+council results
      are reported separately
  - still over-rejecting and under-proven
  - baseline quiet-flow backtest completed 2026-04-22:
    - scanner-only, 2020-2024, 594 signals
    - 3d WR 41.8%, avg adjusted return -0.15%, stop-hit 34.7%
    - 5d WR 42.8%, avg adjusted return -0.03%, stop-hit 42.9%
    - controls dominated: 293/594 signals (49.3%), led by AGG/XLP/XLV/SPY/XLI
    - scanner+council replay was not run because historical intraday/quote/sentiment
      state is unavailable without fabricating inputs
  - single-stock-only structural rerun completed 2026-04-22:
    - scanner logic unchanged; ETF/control instruments excluded only from the
      tradable universe and retained as reporting controls
    - 301 single-stock signals, with no single ticker above 5% concentration
    - 3d WR 45.2%, avg adjusted return -0.03%, stop-hit 33.6%, target-hit 6.3%
    - 5d WR 48.5%, avg adjusted return +0.21%, stop-hit 41.2%, target-hit 11.6%
    - control comparison remained weak: 293 control signals, 3d avg adjusted -0.27%
    - removing controls improved signal quality, but did not reveal a stable
      paper-ready edge; 2021-2024 remained weak and 2022 was sharply negative
    - verdict: research-only; stop REMORA for this phase and move the doctrine
      sequence to CONTRARIAN
- `CONTRARIAN`: `AMBER`
  - doctrine audit completed 2026-04-22 under the Quant Research Doctrine
  - identity locked as panic / dislocation / forced-selling rebound, LONG only
  - not generic dip-buying, not SNIPER breakout confirmation, not REMORA
    quiet-flow, not VOYAGER long-horizon accumulation, not SHORT continuation
  - scanner implementation matches identity:
    - VIX gate: active at VIX >= 28, watch mode at VIX >= 22
    - SPY washout gate: >= 3% below 10-day high or SPY RSI < 38
    - stock gate: VIX-adjusted RSI ceiling (`42 -> 38 -> 35`), price not below
      80% of MA50, at least one reversal-quality signal
    - stop/target: stop = 1.5x ATR, target = 2.25x ATR, half-size position
  - scanner/council fit is PARTIAL:
    - production still routes CONTRARIAN through generic `VetoCouncil`
    - Tier 2 LONG scoring can penalize negative sentiment and negative momentum,
      which are normal in panic-rebound setups
    - tolerable for scanner-only baseline validation, but scanner+council replay
      must be reported separately and not treated as decisive unless historical
      council inputs are available without fabrication
  - data integrity is PARTIAL for full production equivalence:
    - daily OHLCV/VIX/SPY path is defined for scanner-only backtest
    - historical live-council inputs and company-specific bad-news separation
      remain validation gaps
  - ready for backtest design next:
    - scanner-only baseline completed 2026-04-22, 2020-2024, 715 signals
    - historical VIX used a 20d annualized SPY realized-volatility proxy because
      no local FMP historical VIX cache exists
    - 3d WR 49.2%, avg adjusted return -0.33%, stop-hit 24.5%, target-hit 6.7%
    - 5d WR 42.8%, avg adjusted return -0.87%, stop-hit 37.2%, target-hit 14.5%
    - 2022 worked modestly: 3d avg adjusted +0.46%, 5d avg adjusted +0.24%
    - 2020 failed badly: 3d avg adjusted -2.65%, 5d avg adjusted -4.17%
    - extreme panic bucket (`VIX proxy >= 35`) was the main failure:
      63 signals, 3d WR 11.1%, avg adjusted -7.54%, stop-hit 84.1%
    - ETF controls were weak: 191 signals, 3d avg adjusted -0.73%, 5d -0.98%
    - idiosyncratic bad-news proxy flagged 82/715 signals (11.5%), so company-
      specific traps exist but were not the dominant measured failure
    - extreme-panic structural variant pass completed 2026-04-22:
      - Variant A excluded VIX-proxy >= 35 and improved 3d avg adjusted from
        -0.33% to +0.37%, with stop-hit down from 24.5% to 18.7%
      - Variant A still had negative 5d/10d adjusted returns (-0.21% / -0.66%)
      - Variant B required next-day stabilization only for VIX-proxy >= 35
        and kept only 5 extreme-panic signals; those still lost badly
        (3d avg adjusted -3.66%)
      - conclusion: the extreme-panic bucket is the true drag, but the simple
        stabilization rule does not rescue it
    - verdict: research-only; if CONTRARIAN continues later, the only supported
      structural change is to stand down above VIX-proxy 35 unless a separate
      extreme-crisis doctrine is created
- `OPTIONS_PHASE_A`: `AMBER / RED`
  - isolated defined-risk spreads overlay is now implemented
  - paper-first lifecycle, logging, and non-fatal `V3` integration are in place
  - fresh live diagnostics now show the options layer is fail-closing for
    specific reasons rather than silently starving:
    - `VOYAGER`/options-universe bull put credits are increasingly failing on
      `credit_width_too_low` during `FEAR_OPPORTUNITY` / `VIX 30+` sessions,
      which suggests a real regime-structure mismatch rather than a plumbing
      bug
    - `SHORT` equity approvals on smaller-cap names are often failing options
      qualification on `no_short_leg_candidate`, confirming that the stock
      short universe and the options-short universe are not the same thing
    - `CONTRARIAN` can still correctly stand down on day-one washout when
      accumulation/stabilization is absent, even if macro washout context is
      valid
  - next options-routing question is now explicit: in `FEAR_OPPORTUNITY`, do
    we disable `VOYAGER` bull put credits and re-route options emphasis toward
    liquid bearish call-credit structures instead of forcing bullish premium
    selling into panic skew
  - live capital promotion is not justified until paper outcomes and broker
    behavior are validated end to end
- `BEST_IDEA_RANKING`: `AMBER / RED`
  - transparent ranking and candidate snapshot tooling is now live
  - current output correctly surfaces `SHORT` near-misses and `REMORA` quality
    names ahead of weak `SNIPER` setups
  - trade-entry forecasting now uses a shadow-rate forecaster first and falls
    back to the legacy trade-history model, which is a more honest fit for the
    current evidence base
  - forecast calibration remains poor (`forecast_validation_report.py`) because
    only a handful of live trades carry forecast fields; the new layer should
    improve that, but it still needs forward validation before autonomous
    capital logic is justified


### Layer 4: Capital Promotion Readiness

Definition:
- system is suitable for meaningful live capital allocation
- strategy behavior is proven with clean forward evidence
- runtime and telemetry are both green
- promotion is strategy-specific, not assumed globally

Current status:
- `RED`

Current position:
- the infrastructure is approaching production-candidate quality
- the strategy stack is not yet broadly promoted for autonomous live capital


## What Is Proven vs What Is Not

### Proven

- canonical runtime path is defined and working
- startup/readiness gate exists
- tests are green
- dynamic shared universe is live
- decision/trade/shadow telemetry is materially stronger than before
- Phase A options spreads overlay is implemented with isolated DB tables and
  non-fatal runtime integration
- forward RR and direction handling are correct for Sniper, Short, Remora, and
  shadow outcome logic
- shared VIX retrieval now uses a centralized live/proxy helper so runtime and
  diagnostics can read the same volatility source
- a transparent best-idea ranking workflow now exists for operator review and
  evidence capture, and its score is now used to order approved trades inside
  the live auto trader
- a shadow-rate forecaster now feeds entry forecasts from resolved shadow
  outcomes (policy/regime-conditioned where possible) instead of relying solely
  on the sparse 17-trade live history
- shadow tracking now supports parallel `5d` and `20d` horizons, so short-term
  execution quality and longer-horizon thesis quality can be measured
  separately
- focused bottleneck tooling now exists via `strategy_bottleneck_report.py` so
  Contrarian activation, Short RR geometry, and Remora score starvation can be
  reviewed directly from fresh DB evidence
- data architecture migration completed 2026-04-21 (three phases):
  - Phase 1: Voyager `scan()` now prefetches fundamentals once per cycle into
    `fund_map` before the ticker loop; `_fundamental_score()` no longer calls
    FMP per-ticker — eliminates ~150 FMP/gatekeeper hits per scan
  - Phase 2: `BacktestDataLoader` (`research/backtests/backtest_data_loader.py`)
    with parquet cache at `cache/backtest_prices/`; all four backtest scripts
    (sniper, voyager_v2, short_v1, short_b) now use it — zero Alpaca calls on
    repeated runs after first warmup
  - Phase 3: SPY regime slot added to gatekeeper (`get_spy_bars()` /
    `put_spy_bars()`, 4h TTL, `cache/prices/SPY_regime.parquet`, merge-on-write
    to preserve multi-year history); `scripts/prefetch_backtest_data.py` CLI to
    warm backtest cache for any universe + date range
  - sanity checks passed: cold-start backtest run 2 = 100% cache, 0 Alpaca
    calls, 0.85s; SPY regime cache verified at 1,755 bars with 200d MA
    computable; Voyager endpoint log confirms zero FMP calls during backtest runs

### Not Proven

- consistent positive edge across the full strategy stack
- options spread edge or live broker behavior for multi-leg orders
- regime-conditional options routing is still incomplete:
  - the engine can now explain why spreads fail, but it does not yet fully
    express the higher-level rule that equity-approved small-cap shorts should
    usually remain stock shorts while options shorts should be sourced from a
    separate liquid-chain universe
  - similarly, `VOYAGER` bullish credit structures are not yet explicitly
    suppressed in `FEAR_OPPORTUNITY`; they are only failing downstream on
    spread economics
- live short lifecycle proof from actual short entries to exits
- broad strategy promotion for autonomous capital
- forecast calibration strong enough to trust probability outputs with size
- any claim that the system is already "best in the world"


## Change-Control Rules

Effective immediately:

1. No strategy threshold or gate change without a pre-declared reason.
2. No strategy change based on legacy, synthetic, or excluded test trades.
3. No strategy change without naming the metric that should improve.
4. No strategy change without a rollback condition.
5. Documentation must be updated with any production-affecting change.
6. Runtime/safety fixes can proceed immediately when they remove a real defect.
7. Telemetry fixes can proceed immediately when they remove a real blind spot.
8. Strategy logic changes require explicit review against this file first.


## Current Build Order

Before further strategy tuning:

1. Keep runtime and telemetry stable.
2. Validate the new options overlay in paper mode before any live activation.
3. Collect fresh forward market sessions on the corrected V3 path.
4. Apply the Quant Research Doctrine sequence strategy by strategy:
   SNIPER verified first, VOYAGER audited next, then REMORA, then CONTRARIAN.
5. Promote or revise strategies one by one, not all at once.


## Definition Of "Production Level"

For this project, "production level" means:

- the daemon is operationally reliable
- logs and analytics are truthful
- safety gates fail closed
- strategy promotion decisions are evidence-based
- claims stay inside what the data has actually proven

That is the standard to build to.
