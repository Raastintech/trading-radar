# ROADMAP_PHASES

The phase map for Stock Lens / `trading-production`. Read alongside
`PROJECT_INDEX.md`. This document is the single place where phase
completion state and the next operating mode are tracked.

> **Snapshot date:** 2026-06-16
> **Current operating mode:** Research-Only — permanent (Phase 4A.3 complete 2026-06-16)
> **Next build phase:** Phase 4B — Forward Evidence and Bucket Performance (gated: need ≥10 matured/bucket)
>
> **Auto-trading, paper-trading, and all execution paths are permanently decommissioned.**
> The system runs daily research cycles (heartbeat, scanner, research cards, lens, forecast)
> via FMP + Tradier (options research). Alpaca is not required. SNIPER and VOYAGER sleeves
> are DECOMMISSIONED. No new paper signals. No capital promotion path open.
> See `docs/research/AUTO_TRADING_DECOMMISSION_FINAL_FINDINGS.md` for the full record.

---

## Summary table

| Phase | Title | Status |
|---|---|---|
| 0 | Safety Foundation | ✅ complete |
| 1A | Operability / Safety Wiring | ✅ complete |
| 1B | Risk Telemetry | ✅ complete |
| 1C | Paper-State Hygiene | ✅ complete |
| 1D | Legacy Quarantine + Clean Epoch | ✅ complete |
| 1E | Clean-Epoch Visibility + Broker Snapshot | ✅ complete |
| 1F | Research Dashboard Consistency + Fragility Overlay | ✅ complete |
| 1G | Stability Window (1G.1–1G.17 research phases) | ✅ complete |
| 2A | Stock Lens MCP Audit Server V1 | ✅ complete |
| 2B | MCP Audit Workflows | ✅ complete |
| **3A** | **Auto-Trading Decommission (execution → research-only)** | ✅ **complete 2026-06-13** |
| **3B** | **Alpaca Dependency Removal + Phase 4/5 Research Engine** | ✅ **complete 2026-06-14** |
| **4A** | **Alpha Radar Research Engine** | ✅ **complete 2026-06-15** |
| **4A.2** | **Alpha Radar Quality Gates** | ✅ **complete 2026-06-16** |
| **4A.3** | **Scanner Field Coverage and Candidate Enrichment** | ✅ **complete 2026-06-16** |
| **4A.5** | **Foundation Audit (adversarial)** | ✅ **complete 2026-06-16** |
| **4A.6** | **Benchmark-Relative Returns in Forward Tracker** | ✅ **complete 2026-06-16** |
| **4B** | **Forward Evidence and Bucket Performance** | ⏳ FUTURE (gated: ≥10 matured/bucket) |
| — | Capital promotion / paper execution | 🚫 **permanently closed** |

Legend: ✅ done · 🚫 closed/not applicable in research-only mode.

---

## Phase 0 — Safety Foundation
**Status:** complete.

- DB hardening (WAL, `synchronous=FULL`, hardened connection helper).
- Voyager lookback audit (paper data correctness).
- Order fill correctness — broker fill price/qty captured, slippage telemetry
  populated, no submit-time `position_opened=1` for unfilled orders.
- Submission-time gate re-check (`core/submission_gate.py`) re-evaluates
  council / regime / circuit breaker / portfolio risk against live state
  immediately before any broker call.
- Pre-registered holdout doc: `docs/research/PRE_REGISTERED_HOLDOUT_2026H2.md`.

## Phase 1A — Operability / Safety Wiring
**Status:** complete.

- Paper-safe defaults: `PAPER_TRADING=true`, `ALPACA_PAPER=true`,
  `ALLOW_LIVE_CAPITAL=false`.
- Live-capital three-key gate inside `core/alpaca_client.py`
  (`PAPER_TRADING`, `ALPACA_PAPER`, `ALLOW_LIVE_CAPITAL` + optional
  `LIVE_CONFIRM_FILE`).
- Service restart verification, `scripts/check_phase1a_live.sh`.
- Circuit breaker wired into `OrderManager` and persisted in the DB
  (`circuit_breaker_state`).
- Reconciler foundation: per-cycle compare of broker `get_positions` vs
  open `decisions` rows; hard drift halts new entries.
- Heartbeat, deadman watcher, regime freshness logging.

## Phase 1B — Risk Telemetry
**Status:** complete.

- `research/slippage_telemetry_report.py`
- `research/portfolio_concentration_report.py`
- `research/shadow_sizing_report.py` (vol-target borrow-adjusted)
- Dashboard risk telemetry strip (Mode 3).
- All four reports are **cache-only**; no provider calls.

## Phase 1C — Paper-State Hygiene
**Status:** complete.

- `research/paper_state_hygiene_report.py` writes
  `cache/research/paper_state_hygiene_latest.json` and
  `logs/paper_state_hygiene_latest.txt`.
- `ready_to_gate_all` (full ledger view) and `ready_to_gate_clean`
  (clean-epoch view) are published as verdicts but **not** active gates.
- **No mutation** — the report reads `SELECT`-only.

## Phase 1D — Legacy Quarantine + Clean Epoch
**Status:** complete.

- `CLEAN_PAPER_EVIDENCE_START` single source: `core/paper_evidence_epoch.py`
  (currently `2026-05-08T00:00:00+00:00`).
- `data/state/paper_legacy_quarantine.json` lists pre-epoch rows that are
  visible but intentionally outside `ready_to_gate_clean`.
- All Phase 1B/1C reports publish dual-scope: full ledger + clean epoch.
- Full ledger keeps legacy debt visible; clean epoch is what future gating
  will reference.

## Phase 1E — Clean-Epoch Visibility + Broker Snapshot
**Status:** complete.

- `scripts/snapshot_broker_positions.py` — operator-invoked read-only
  Alpaca `get_positions` snapshot to `cache/state/broker_positions_snapshot.json`.
- Quarantine enrichment: hygiene report fills `broker_position_match`
  per legacy row.
- Dashboard `RISK TELEMETRY` strip surfaces Phase 1D verdicts and Phase 1E
  match coverage.
- No new gates, no DB mutation, no live trading.

## Phase 1F — Research Dashboard Consistency + Fragility Overlay
**Status:** complete.

- `core/fragility.py` — display-only cross-artifact evaluator producing
  `NORMAL / CONFLICTED / FRAGILE / STRESS / UNKNOWN`.
- Forecast panels lead with FRAGILE/CONFLICTED when breached or LOW conf.
- REGIME CONFLICT badge in Research Assist when posture is bullish but
  forecast is not confirmed.
- "BUY candidate / aligned now" wording replaced with
  "Research-aligned candidate / research-aligned".
- Entry-wording discipline: candidate labels demoted when the entry layer
  is Too Extended / Broken / Avoid.
- Mode 2 `RESEARCH STATE` strip + Mode 1/3 propagation.
- 30d trend bias computed from cached SPY bars; alongside 5d/10d in all
  modes.
- Stale research-note auto-fallback to `[auto]` panel.
- Strategy / scoring / governance / execution untouched. Live trading
  still disabled.

---

## Phase 1G — Stability Window
**Status:** 🟢 **next operating mode (now).**

**Purpose.** Run the system for **2–4 weeks** with no new feature drift.
Confirm the operational substrate stabilized in Phases 1A–1F holds under
ordinary daily use.

**Activities:**

- Run the system continuously; collect paper evidence into the clean epoch.
- Observe **slippage telemetry** (Phase 1B) — is the bps distribution
  stable across days?
- Observe **portfolio concentration** — does the 5%/single-name cap ever
  trip the council heat pre-check (Phase 1F+) in practice?
- Observe **reconciler drift** — `RECONCILE OK` should be the norm; any
  `RECONCILE_DRIFT` audit row is a diagnostic event to investigate.
- Observe **broker snapshot freshness** — the daemon refreshes
  `cache/state/broker_positions_snapshot.json` every reconcile cycle
  (Phase 1F+); confirm it stays within minutes of now during market hours.
- Audit the **fragility overlay** — does FRAGILE / CONFLICTED correctly
  flip on regime breach events?

**Explicit "do nothing" list during 1G:**

- No new alpha logic.
- No new scanner thresholds.
- No new MCP wiring.
- No scoring tweaks.
- No "just one more" feature.

**Exit criteria:** 14–28 trading days of clean-epoch evidence with no
unresolved structural drift, and an operator decision to begin Phase 2A.

**Safety patches during 1G:**

- **2026-05-16 — broker-unavailable patch.** Reconciler now distinguishes
  broker read failure from a genuine empty broker book; false drift from
  transient DNS/API failure fixed. `AlpacaClient.get_positions_with_status`
  returns `(positions, ok)`; reconciler emits `BROKER_UNAVAILABLE` instead
  of synthesizing full-book drift on a failed call; `main.py` tolerates up
  to `PROVIDER_OUTAGE_HALT_AFTER_CYCLES=6` consecutive failures before
  halting. Covered by `tests/unit/test_reconciler_broker_unavailable.py`.

---

## Phase 1G.1 — Operational Reliability Fixes
**Status:** 🟢 **landed 2026-05-18.**

**Trigger.** Mid-1G audit found the system halted but the underlying
drift resolved: a SHORT CRK exit had been booked `position_closed=1`
the moment `alpaca.close_position()` *returned*, before the broker had
actually flattened. Days later the broker filled the close in stages
(-234 → -128 → 0), the reconciler saw `BROKER_ONLY` drift, the
operator-only halt tripped, and the system sat flat for hours after the
drift had cleared on its own. The audit also surfaced a stale Monday-
morning forecast anchor flagged as "behind" by raw calendar age, soft
council scores logged at -1685 / -1896 from an unbounded flow-agent
math branch, and small-sample slippage / concentration "warnings" that
were mechanically extreme but unactionable.

**Scope (no doctrine changes, no new gates):**

- `scripts/review_halt_state.py` — read-only operator workflow. Default
  mode prints a halt verdict by cross-checking
  `circuit_breaker_state`, the broker snapshot, open decisions, recent
  reconciler rows, and `paper_state_hygiene_latest.json`. Adding
  `--clear-reviewed --reason "..."` only clears the breaker when ALL
  preconditions pass (broker/book match, no active drift, snapshot
  fresh, `ready_to_gate_clean=true`, operator-typed reason). Never
  auto-clears. Never raises a halt.
- **Close-lifecycle correctness.** `execution/position_monitor.py`
  polls the close order to terminal state, classifies the outcome
  (`filled` | `partial` | `pending`), and only writes `log_exit` on a
  full fill. Partial fills / pending orders mark the decision row
  `suspect_state='closing_in_progress'` and keep the row OPEN so the
  reconciler keeps it in scope. `core/decision_logger.py` gains
  `mark_suspect`. Exit conditions, scanner logic, scoring, and the
  live-capital gate are unchanged — only bookkeeping was wrong.
- **Forecast freshness guard.** `research/regime_forecast.py` now
  compares the anchor to the most recent *completed* NYSE trading day
  (`_expected_anchor_date`), not raw calendar age. Adds
  `expected_anchor_date`, `expected_anchor_basis`, and `forecast_state`
  fields (`FRESH` | `FRAGILE_STALE` | `STALE` | `MISSING`).
  Monday-premarket forecasts that anchor on Friday's close now read
  `FRESH`. A post-close cycle that still has yesterday's anchor reads
  `FRAGILE_STALE` (informational lag). Anchors that miss completed
  closes still read `STALE`. Forecast scoring is unchanged.
- **Council score invariant.** `council/veto_council.py` documents the
  Tier-2 score range as `[0, 100]`, adds `_safe_agent_score` to clamp
  in-range values, adds `_validate_votes` to flag off-scale inputs,
  and emits a `SCORE_ANOMALY` safe veto when any agent returns a
  score outside the range. The `_flow_agent` math is fixed at the
  source (lower-bound clamp on the `(1 - vol_accel) * 50` branch);
  prior unbounded behavior produced the -1685 / -1896 totals seen in
  the audit. No threshold tuning — verdicts still hinge on
  `MIN_SOFT_SCORE=50`.
- **Telemetry calibration.** Slippage and concentration reports
  separate actionable WARNINGS from sample-size INFO. Slippage breach
  with `n < SLIPPAGE_MIN_SAMPLE_FOR_WARNING (10)` is reported as
  `INSUFFICIENT_SAMPLE` info, not a warning. Concentration with
  `n_positions < CONCENTRATION_MIN_POSITIONS_FOR_WARNING (3)` reports
  the mechanical top-N share as `SINGLE_POSITION_BOOK` info. Both
  reports now publish an `info` list alongside `warnings` in the JSON
  sidecar; same numerical thresholds drive WARNING once the
  sample-size gate clears. No execution-side impact.

**Tests added:**

- `tests/unit/test_phase_1g1_review_halt_state.py`
- `tests/unit/test_phase_1g1_close_lifecycle.py`
- `tests/unit/test_phase_1g1_forecast_freshness.py`
- `tests/unit/test_phase_1g1_council_score_invariant.py`
- `tests/unit/test_phase_1g1_telemetry_calibration.py`
- updates to `tests/unit/test_portfolio_concentration.py` for the new
  warning vs info gate.

**Explicit non-changes:**

- No strategy threshold edits (holdout parameters remain frozen).
- No scanner logic edits.
- No governance / submission-gate edits.
- No automatic breaker clearing.
- No new DB writes outside the optional `mark_suspect` call from the
  position monitor (additive, nullable column).
- No live-trading enablement; `ALLOW_LIVE_CAPITAL` remains absent.

---

## Phase 1G.2 — Signal Hygiene + Legacy Policy + Halt Usability
**Status:** 🟢 **landed (pre-2026-05-24).**

Documented retroactively. Scope (no doctrine changes, no new gates):

- `core/signal_hygiene.py` — duplicate-suppression (`find_recent_duplicate`,
  `setup_state_hash`), pre-size guard (`compute_presize_verdict`), and the
  documented SHORT_A regime gate (`compute_short_regime_verdict`), plus
  `HygieneCounters` → `cache/research/signal_hygiene_latest.json`.
- `research/legacy_decision_policy_report.py` — read-only policy report for
  pre-clean-epoch `decisions` rows missing fill data (keep-quarantined default).
- `scripts/review_halt_state.py` — `--dry-run-and-print-decision`,
  `clear_eligible`, required `--reason`, active-drift blocks clear, historical
  resolved drift clear-eligible only with fresh broker/book match.
- Forecast/stock-lens forward snapshot logging + resolvers
  (`core/forecast_forward_tracker.py`).

## Phase 1G.3 — Evidence Closure + SHORT_A Freeze + Short Radar + LEADER_RESET Validation
**Status:** 🟢 **landed 2026-05-24.**

**Trigger.** The Strategy Truth Review (`docs/strategy/STRATEGY_TRUTH_REVIEW_2026_05.md`)
found 0 strategy-opened positions in 7d, SHORT_A emitting ~99.6% re-emission noise
into a bull tape, VOYAGER favored but not converting, SNIPER n=1, and forward
outcomes not yet maturing. This phase cleans the evidence stream — it does **not**
produce trades.

**Scope (research/evidence hygiene only; no execution/governance/registry-activation):**

- **SHORT_A frozen → research-only.** `core/strategy_registry.py`: SHORT
  `active_paper → frozen`. This removes `short` from `active_scanner_keys()` (the
  scanner no longer runs in `main.py`) and makes `is_active_paper_strategy("SHORT")`
  false (paper governance rejects). **Historical SHORT_A rows are preserved and keep
  resolving** via new paper-ledger helpers (`paper_ledger_strategies/tags/horizons`)
  consumed by `research/paper_trades/resolve_tactical_outcomes.py`. Dashboard labels
  updated; SHORT_A now shows under "Frozen this phase".
- **Short Opportunity Radar** (`research/short_opportunity_radar.py`) — research-only
  `SHORT_REGIME_SCORE` 0–100 → `SHORTS_OFF / WATCH / RESEARCH_ACTIVE /
  SHORT_SLEEVE_TEST_CANDIDATE`, archetype classification, no trade language, no paper
  signals. Sidecars: `short_opportunity_radar_latest.{json,txt}`.
- **Forward-resolution health** (`research/forward_resolution_health.py`) — diagnosis:
  resolver is healthy; `matured=0` is a definitional artifact (20d horizon needs 31
  calendar days; oldest anchor 27d). First maturity due **2026-05-28**. Sidecars:
  `forward_resolution_health_latest.{json,txt}`.
- **LEADER_RESET event study** (`research/leader_reset_event_study.py`) — research-only
  classification of the lens forward log into candidate states + forward cohorts.
  Verdict **NEED_MORE_DATA** (RESEARCH_READY n=0: the entry validator never emits an
  actionable reclaim — the structural reason the system opens nothing). Outputs
  `docs/research/LEADER_RESET_EVENT_STUDY_SUMMARY.md` + sidecars. **No paper sleeve,
  no signals, no registry change.**
- **VOYAGER conversion audit** (`research/voyager_conversion_audit.py`, read-only) —
  ~2.1% approval→signal conversion (3 signals / 145 approvals); enters extended names;
  2/3 signals logged against 13F SELLING. Recommends folding the long-leadership thesis
  into LEADER_RESET rather than redesigning VOYAGER independently. No code change.
- **Legacy decision policy** doc (`docs/ops/LEGACY_DECISION_POLICY.md`) — 10 pre-clean
  rows, all closed/closed-by-book, keep-quarantined; `ready_to_gate_clean` unaffected.
- **MCP surfacing** — `research/mcp_audit_orchestrator.py` adds a cache-only
  `phase_1g3` block + markdown section (SHORT_A status, radar, forward resolution,
  LEADER_RESET verdict, VOYAGER conversion). Dashboard remains cache-only.
- `scripts/run_research_cycle.sh` — radar + forward-health added to `risk-telemetry`;
  new subcommands `short-radar`, `forward-health`, `leader-reset-study`, `voyager-audit`.

**Tests added:** `tests/unit/test_phase_1g3_{short_freeze,short_radar,forward_resolution_health,leader_reset_event_study,halt_dry_run_no_mutation,mcp_surfacing}.py`.

**Doctrine note:** *No active short sleeve is better than a bad active short sleeve.*
The system may run long-only / cash while the short side is under research. LEADER_RESET
stays research-only until its event study **and** the formal validation-plan backtest
clear every activation gate.

**Explicit non-changes:** no live trading, no execution / OrderManager / governance /
submission-gate edits, no new active strategy registered, no LEADER_RESET paper signals,
no new SHORT_A emissions, no threshold tuning, no DB mutation beyond the existing
designed forward resolvers, no auto-clear of the circuit breaker, no provider calls from
the dashboard. Phase 2C remains not started.

## Phase 4A — Alpha Radar Research Engine
**Status:** ✅ **complete 2026-06-15.**

Upgraded the research scanner into a proper Alpha Radar. New components:

- `research/research_scoring.py` — `earliness_label()` + `consensus_label()` (lifecycle + signal-breadth scoring)
- `research/research_coverage_audit.py` — per-ticker data confidence (HIGH/MEDIUM/LOW/INVALID)
- `research/research_change_detector.py` — daily watchlist delta (new/dropped/score/label)
- `research/research_watchlist_forward_tracker.py` — forward outcome tracking by label bucket
- `research/ten_x_candidate_radar.py` — speculative 10x candidate radar
- `scripts/run_research_cycle.sh` — five new subcommands (`research-coverage`, `research-scanner`, `research-changes`, `research-forward-tracker`, `ten-x-candidates`)

Scanner now emits `earliness_label`, `consensus_label`, and `all_categories` on every watchlist item. Commit: `94f225d`. Doctrine: `docs/research/PHASE_4A_ALPHA_RADAR_OVERVIEW.md`.

**No trade recommendations, no signals, no execution.**

---

## Phase 4A.2 — Alpha Radar Quality Gates
**Status:** ✅ **complete 2026-06-16.**

Hardened the Alpha Radar against false high-priority labels, legacy strategy noise, and missing data propagation. Built on Phase 4A.1.

**What shipped:**

- `research/research_scoring.py` — `priority_label()` (9-label cascade: TOP_RESEARCH, HIGH_PRIORITY_RESEARCH, WATCHLIST_RESEARCH, RESET_WATCH, RECLAIM_WATCH, CONFLICTED_SIGNAL, EXTENDED_CROWDED, DATA_QUARANTINE, INVALID_PRIORITY), `earliness_detail()` (missing-field tracking + extension_state), `quality_adjusted_consensus()` (score penalties for UNKNOWN earliness, LOW confidence, social-only, extension, conflicts)
- `research/ten_x_candidate_radar.py` — V2: `TRUE_10X_RESEARCH` vs `ASYMMETRIC_RECOVERY_WATCH` vs `THEME_ONLY`; stricter structural criteria; AI/ALOY correctly land in ASYMMETRIC
- `research/catalyst_sanity.py` — new: freshness/syndication/sector-spillover/malformation validator, 7 output labels, `can_upgrade` gate
- `research/daily_alpha_radar_report.py` — new aggregator: 14-section markdown report with per-candidate priority/earliness/conflict/downgrade/why/confirms/invalidates; options coverage guard (disabled below 50%); writes `docs/research/DAILY_ALPHA_RADAR_REPORT.md` + JSON sidecar
- `research/research_watchlist_forward_tracker.py` — sample-status gates (TOO_EARLY/PROVISIONAL/MEANINGFUL/ROBUST with 10/30/100 thresholds)
- `scripts/run_research_cycle.sh` — `cmd_daily_alpha_radar()` + `daily-alpha-radar` dispatch; full Phase 4A chain (scanner/coverage/changes/forward-tracker/ten-x/daily-radar) wired into `cmd_nightly`
- `tests/unit/test_phase4a2_quality_gates.py` — 72 new tests; 1514 total passing
- `core/research_mode.py` — fixed pre-existing `RESEARCH_ONLY_BANNER` adjacent-literal bug (body was silently repeated ×70)
- `docs/research/ARCHIVED_STRATEGY_DIAGNOSTICS.md` — reference for legacy tools excluded from radar

**Forbidden grep check (post-nightly):** zero legacy strategy terms, zero trade-action terms in `DAILY_ALPHA_RADAR_REPORT.md`. Commit: `35e0683`.

**Known remaining gap (Phase 4A.3):** `above_ma200` is only populated by the scanner for ~21% of watchlist categories, causing 79% of candidates to land in DATA_QUARANTINE. The gating logic is correct; the scanner field coverage needs extending.

**No trade recommendations, no signals, no execution.**

---

## Phase 4A.3 — Scanner Field Coverage and Candidate Enrichment
**Status:** ✅ **complete 2026-06-16.**

**Goal:** Reduce DATA_QUARANTINE caused by missing scanner fields and make every watchlist candidate fully scorable.

**Main problem:** 41/52 candidates were quarantined in Phase 4A.2 because the scanner did not populate required technical fields (especially `above_ma200`) across all watchlist paths. The quality gates are correct; the enrichment is incomplete.

**Scope:**

1. Populate required technical fields for all scanner candidates: `above_ma20`/`above_ema20`, `above_ma50`, `above_ma200`, `dd_from_high_pct`, `vol_trend_ratio`, `rs_20d_vs_spy`, `rs_63d_vs_spy`, `extension_vs_ma20`/`extension_vs_ema20`, `extension_state`, liquidity/avg dollar volume, sector/industry, `ticker_valid`
2. Reduce UNKNOWN earliness — UNKNOWN must only appear when data is truly unavailable, not when the scanner simply skipped the field
3. Wire catalyst sanity per item (`headline`, `source`, `published_at`, `seen_in_sources`, `catalyst_sanity_label`, `catalyst_can_upgrade`) — `validate_catalyst()` is already imported in `daily_alpha_radar_report.py` but not yet called per-item
4. Improve Data Quarantine handling: distinguish `DATA_INCOMPLETE` (enrichable) from `INVALID` (unscorable)
5. Update Daily Alpha Radar to surface fewer quarantined names as enrichment improves

**Acceptance criteria:** DATA_QUARANTINE count drops materially due to enrichment (not weaker gates); `above_ma200` exists for all candidates with enough price bars; earliness UNKNOWN rate drops materially; no HIGH_PRIORITY candidate has missing required fields; tests pass; zero trade language; system remains research-only.

**What shipped:**

- `research/research_candidate_enrichment.py` — new central enrichment module: `enrich_research_candidate()` (fills all required technical, liquidity, metadata fields), `classify_quarantine_subtype()` (5 sub-types: INVALID_PRIORITY, INSUFFICIENT_HISTORY, LOW_LIQUIDITY, DATA_INCOMPLETE, DATA_QUARANTINE), deep cache preference (`cache/prices_deep/`)
- `research/research_scanner.py` — `build_scanner()` now calls central enrichment post-deduplication; profiles fetched for ALL items (not just top 25)
- `research/daily_alpha_radar_report.py` — imports `classify_quarantine_subtype`; `_enrich_with_priority()` uses item's own `ticker_valid`/`liquidity_ok` when coverage sidecar absent; new `Scanner Field Coverage` section; quarantine subtype breakdown; per-candidate shows `earliness_score`, `sector`, `liquidity_ok`, `missing_fields`
- `tests/unit/test_phase4a3_field_coverage.py` — 66 new tests; 1580 total passing
- `docs/research/PHASE_4A3_SCANNER_FIELD_GAP_AUDIT.md` — before/after field gap analysis
- `docs/research/SCANNER_CANDIDATE_ENRICHMENT_SPEC.md` — enrichment contract and rules

**Results (post-nightly):** above_ma50/above_ma20/rs_63d/rs_20d/dd/vol_trend/data_confidence/ticker_valid/liquidity_ok/sector: all 100% populated. above_ma200: 21%→38% (correctly unavailable for recent IPOs). Earliness UNKNOWN: 41→32. DATA_QUARANTINE: 41→34. Zero trade/legacy terms.

**Known remaining gap:** `validate_catalyst()` is imported but not called per-item (scanner categories don't provide `headline`/`published_at`/`source`). Recent IPOs (<200 bars) correctly remain as INSUFFICIENT_HISTORY — no fix possible without historical data.

**No trade recommendations, no signals, no execution.**

---

## Phase 4B — Forward Evidence and Bucket Performance
**Status:** FUTURE

**Goal:** Use matured forward tracker observations to identify which research buckets actually produce useful forward outcomes.

**Do not start Phase 4B until:** ≥10 matured observations per bucket for a provisional read; ≥30 for meaningful; ≥100 for robust. Current state as of 2026-06-16: n=0 matured (TOO_EARLY).

**Metrics to evaluate:** 5d/10d/20d/60d forward return; return vs SPY; return vs sector ETF; MFE/MAE; false-positive rate; early vs late classification; best/worst bucket combinations.

**No trade recommendations, no signals, no execution.**

---

## Phase 2A — Stock Lens MCP Audit Server V1
**Status:** 🟦 **next build phase (planned).**

Read the full plan in `docs/ops/MCP_AUDIT_SERVER_PLAN.md`. Summary:

- Read-only MCP interface for Claude.
- Cached-artifact inspection only.
- Contradiction audits (forecast vs lens, posture vs forecast, alpha vs
  entry validator).
- Late-chase audits.
- **No trading.** No broker order/cancel/close endpoints exposed.
- **No provider calls** unless a future phase explicitly approves.

## Phase 2B — MCP Audit Workflows
**Status:** planned.

- Claude asks structured audit questions ("which tickers are flagged
  research-aligned but have a Bearish-but-oversold lens?").
- Contradiction reports.
- Dashboard consistency reviews.
- **No trade execution.**

### Phase 2B.1 — MCP audit session orchestration
**Status:** complete (2026-05-17).

- `research/mcp_audit_orchestrator.py` composes daily + system_health +
  late_chase + top-10 anomaly drilldowns into a single
  `mcp_analysis_latest.json` sidecar.
- Dashboard Risk-mode `MCP AUDIT SUMMARY` panel reads only that JSON;
  never calls MCP tools, providers, or Claude.
- Same forbidden-import / no-mutation invariants as Phase 2B.

### Phase 2B.2 — Per-ticker freshness + catalyst awareness
**Status:** complete (2026-05-20).

- `core/artifact_freshness.py` adds a uniform freshness contract (age,
  threshold, stale reasons) reused by the dashboard and the
  gatekeeper-refresh workflow.
- Dashboard Executive Gatekeeper panel **suppresses cached "Top reasons"**
  when the artifact is stale (>24h normal / >6h earnings-day / >4h
  intraday warn) and surfaces the exact rerun command instead.
- Dashboard MCP audit panels (`MCP AUDIT SUMMARY`, one-line strip)
  suppress the cached state/keyword when the sidecar is older than 12h,
  the market session has changed since `generated_at`, or
  `regime_forecast_latest.json` is newer than the sidecar.
- Earnings-day badge on the per-ticker frame
  (`EARNINGS TODAY / TOMORROW / THIS WEEK / POST-EARNINGS`) — reads the
  earnings calendar already loaded by DataLayer, no new provider calls.
- `./scripts/run_research_cycle.sh gatekeeper-refresh` batches Executive
  Gatekeeper artifact refresh across open positions, top Alpha
  candidates, earnings tickers, and missing/stale artifacts. Cap 25
  default, `--max N` override.
- **No execution / governance / paper-evidence changes.** Dashboard
  remains cache-only.

### Phase 2B.3 — Gatekeeper refresh cadence + audit ordering
**Status:** complete (2026-05-20).

- `cmd_premarket` now runs `gatekeeper-refresh` after `cmd_delta`, so
  the high-priority short list's Executive Gatekeeper artifacts are
  fresh before the trading session opens.
- `cmd_nightly` runs `gatekeeper-refresh` after `cmd_lenses_nightly`
  and before `cmd_risk_telemetry`, so the next premarket starts from a
  warm cache.
- `cmd_mcp_audit_session` accepts an optional `--refresh-gatekeeper`
  flag that runs `gatekeeper-refresh` BEFORE the orchestrator. Default
  behavior unchanged: no refresh unless explicitly opted in.
- Freshness-first operator order (documented in the script header):
  `forecast / alpha / lens → gatekeeper-refresh → risk-telemetry →
  mcp-audit-session → dashboard review`.
- `gatekeeper-refresh` logs a `selection by source:` breakdown so the
  operator can see why each ticker is in the plan.
- **No execution / governance / strategy / paper-evidence changes.**
  Dashboard still cache-only.

## Phase 2C — Trade Proposal Generator — no execution
**Status:** future / planned. **Slot reserved** — do not retitle this
phase to refer to options data, infrastructure work, or any other
cross-cutting effort. The recent Options Data Enrichment work (Alpaca
primary + Tradier IV/Greeks merge, shipped 2026-05-21) is *not* this
phase; it is listed under "Cross-cutting infrastructure" below.

- Claude generates **paper-only** trade proposals.
- **No broker order submission.**
- Each proposal must reference Stock Lens, Executive Gatekeeper, Entry
  Validator, risk telemetry, and paper hygiene state.

## Phase 2D — Manual-Approved Paper Execution Through Existing Gates
**Status:** future / blocked.

- Allowed only after Phases 2A–2C demonstrably safe.
- Paper only.
- Every order MUST route through `OrderManager` → Submission Gate →
  Circuit Breaker → Portfolio Risk → Reconciler → fill telemetry.
- No direct Alpaca/Tradier execution from Claude/MCP.

## Phase 2E — Limited Paper Automation
**Status:** future / blocked.

- Allowed only if Phase 2D demonstrates safe paper proposals.
- Deterministic gates must pass; clean epoch must remain clean.
- **No live capital.**

---

## Phase 3 — Holdout Monitoring / Evidence Accumulation
**Status:** planned / ongoing.

- Pre-registered holdout window: **2026-06-01 → 2026-12-01**
  (see `docs/research/PRE_REGISTERED_HOLDOUT_2026H2.md`).
- No retuning of strategies inside the holdout.
- No capital promotion until the post-holdout review.

## Phase 4 — Post-Holdout Statistical Review
**Status:** future.

- Out-of-sample evaluation:
  - bootstrap confidence intervals
  - random-controls comparison
  - walk-forward
- Only after this review may a tiny capital pilot be discussed.

## Phase 5 — Optional Capital Pilot
**Status:** future / blocked.

Allowed **only if all of the following hold:**

- Holdout passes the Phase 4 review.
- Clean epoch remains clean (no quarantine events during the window).
- Phase 0/1A safety gates verified by an explicit checklist run.
- Three-key live-capital gate confirmed
  (`PAPER_TRADING=false`, `ALPACA_PAPER=false`, `ALLOW_LIVE_CAPITAL=true`)
  plus a hand-signed operator approval.
- An operator manually approves each step.

---

## Cross-cutting infrastructure (not phase-numbered)

These items are research / data-layer work that ships independently of
the phase progression. They are tracked here so future sessions don't
confuse them with numbered phases.

### Options Data Enrichment — Alpaca primary + Tradier IV/Greeks merge
**Status:** ✅ shipped 2026-05-21.

- `core/options_feed_chain.py::get_chain` keeps "first non-empty wins"
  for primary selection (Alpaca serves the chain shape: OI, quote,
  volume) and now **opportunistically merges** IV + greeks from a
  secondary leaf (Tradier) onto the primary's stubbed-zero rows.
- Per-row, non-destructive: primary values that are already meaningful
  are never overwritten. Only the stubbed-zero rows are filled.
- Mergeable fields: `impliedVolatility`, `delta`, `gamma`, `theta`,
  `vega`, `rho`, `bid_iv`, `ask_iv`. OI, volume, bid, ask, strike,
  `inTheMoney`, `contractSymbol` always come from the primary.
- Primary feed cache isolated by shallow copy so the merge cannot
  pollute a TTL-cached DataFrame returned by reference.
- Diagnostics on `OptionsFeedChain.status()`: `last_served`,
  `last_enriched_by`, `served_counts`, `enrich_counts`,
  `last_zero_iv_before`, `last_zero_iv_after`.
- Live verification on TSLA / AAPL / SPY (2026-05-21): IV coverage on
  TSLA calls moved from 35/167 to 134/167; SPY calls 0/163 → 80/163;
  AAPL calls 13/78 → 76/78. OI / volume / bid preserved on all three.
- Tests: 21 audit-charter cases in
  `tests/unit/test_options_chain_merge_audit.py` + 33 chain tests in
  `tests/unit/test_alpaca_options_client.py`. Full suite: 628/628.
- Consumers: research-only — `research/stock_lens_runner.py`,
  `core/alpha_discovery.py` (research surface), and
  `research/social_arb_radar.py`. **Not** reachable from
  `execution/`, `council/`, `strategies/`, `main.py`.
- **This is not Phase 2C.** Phase 2C remains reserved for the future
  Trade Proposal Generator.
- Doctrine: `docs/ops/OPTIONS_DATA_SOURCES.md`.

---

## Governance

- A phase is **complete** only when its exit criteria are satisfied AND the
  next session re-reads this doc and confirms the state.
- Phases are sequential. A "skip-ahead" is a doctrine violation and must be
  refused unless the operator explicitly approves with the higher phase
  named in the request.
- Changes to phase status here ARE the source of truth for any future
  session.
