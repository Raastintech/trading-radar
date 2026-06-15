# Strategy Tournament / Profitability Discovery Lab — Design

**Phase:** 1G.4
**Status:** Research-only. No paper sleeve, no signals, no registry change, no execution,
no governance, no provider calls, no live-capital change.
**Engine:** `research/strategy_tournament.py`
**Results:** `docs/research/STRATEGY_TOURNAMENT_RESULTS.md` (regenerated each run)

---

## 1. Purpose

We do **not** want a label-fixed system. We want a data-approved, fact-tested system
that can become profitable — and we are open to changing strategy direction entirely if
the evidence demands it. Strategy Truth Review found **0 strategy-opened positions in the
last 7 days**: the infrastructure is strong but the *strategy layer is unproven*.

The tournament compares multiple candidate alpha/option strategy families against the
**same data, same friction, same regime segmentation, same risk rules, and the same
pass/fail gates**, so the comparison is apples-to-apples and cannot be gamed by tuning
one family's labels.

It answers one question honestly: **is any strategy family supported by evidence strongly
enough to earn a paper spec — and if not, what must happen first?**

## 2. Hard guardrails (inherited from Phase 1B–1G doctrine)

- Diagnostic only. Emits **no** orders, paper_signals, trade proposals, or registry rows.
- **No mutation** of `decisions`, `paper_signals`, `paper_signal_outcomes`,
  `voyager_paper_signals`, `veto_log`, or any forward log. The engine only *reads*
  `data/state/stock_lens_forward_log.jsonl` and cache artifacts.
- No production import surface: the module imports only
  `core.forecast_forward_tracker` (read helper), `core.paper_evidence_epoch`
  (clean-epoch cutoff), and the friction constant from
  `research.paper_trades.resolve_tactical_outcomes`. It must **not** import
  `execution/`, `council/`, `core.config`, or any governance module.
- No live-capital settings are referenced or changed.
- Short families stay **research-only** and carry no trade language.

## 3. Data sources (reuse, no new providers)

The only artifact that carries *forward* outcomes per historical setup is the Stock Lens
forward log, so it is the unifying event spine. Other artifacts are read for context.

| Source | Role |
|--------|------|
| `data/state/stock_lens_forward_log.jsonl` | **Event spine.** Each snapshot carries market/sector/tech/entry/alpha/options/posture layers + resolved forward outcomes (1/5/10/20d return, MAE, MFE, rel-SPY). |
| `cache/research/regime_forecast_latest.json` | Current regime / VIX / SPY-vs-MA context (for short-side gating + headline regime). |
| `cache/research/alpha_discovery_board_latest.json` | Current Alpha board (strongest live component) — context + liquidity/options-quality reference. |
| `cache/research/short_opportunity_radar_latest.json` | Short-side state (OFF / RESEARCH / TEST_CANDIDATE). |
| `research.paper_trades.resolve_tactical_outcomes.ROUND_TRIP_FRICTION_PCT` | Shared friction benchmark (0.30% round-trip). |
| `core.paper_evidence_epoch.CLEAN_PAPER_EVIDENCE_START` | Clean-epoch cutoff (2026-05-08). |

**Known data gaps (reported, never fabricated):** per-event earnings-reaction gaps,
per-event QQQ / sector-ETF relative returns, historical options chains (IV / greeks /
spreads / strikes), and 20d forward outcomes are **not** present in the spine. Families
and metrics that depend on them are marked `NOT_ENOUGH_DATA` / `unavailable` rather than
guessed.

## 4. Candidate families

| Key | Side | Maps to data via |
|-----|------|------------------|
| `LEADER_RESET` | long | bullish leader, not extended, reclaim-ready entry (reuses `leader_reset_event_study` classifier) |
| `OPTIONS_EXPRESSION_ON_VALID_SETUP` | long (overlay) | valid long setup **then** options structure choice (Task 7); never options-only |
| `POST_EARNINGS_DRIFT` | long | strong reaction continues — **data gap** (no earnings-reaction field in spine) |
| `OPTIONS_FLOW_CONFIRMATION` | long | bullish spot/tape setup confirmed by `options.view ∈ {Bullish confirming, Bullish positioning}` (excludes Speculative chase / Bullish (late)) |
| `13F_EMERGING` | long | `alpha.track == "Emerging Opportunity"` and **not** crowded bucket |
| `FAILED_LEADER_SHORT` | short (research) | former leader (alpha track) now bearish/breaking, weak regime |
| `RISK_OFF_RELATIVE_WEAKNESS_SHORT` | short (research) | only in fragile/risk-off — **no sample** in current bull tape |
| `CASH_NO_TRADE` | cash | flat baseline: net expectancy 0, MAE 0 |
| `SIMPLE_MOMENTUM_BASELINE` | long | simple rule: bullish tech / positive RS, ignore entry quality |
| `RANDOM_LIQUID_CONTROL` | long | deterministic seeded random sample of all snapshots (no lookahead) |

## 5. Standardized event row (Task 3)

Every eligible snapshot becomes one event row with: `ticker, event_date, strategy_family,
regime_state, sector_state, entry_state, options_quality, gatekeeper_status,
earnings_proximity, liquidity_spread_quality, proposed_stop, proposed_target,
risk_at_stop, heat_cap_fit, reason_codes, reject_codes, label` where
`label ∈ {RESEARCH_CANDIDATE, WATCH, BLOCKED, NO_EDGE, NOT_ENOUGH_DATA}`.

Fields absent from the spine (`gatekeeper_status`, `earnings_proximity`,
`liquidity_spread_quality` when the row lacks them) are set to `"unknown"` — never
invented.

## 6. Forward outcomes (Task 4)

Per event, from the snapshot's recorded outcomes: 1d/3d (not in spine → null), 5d, 10d,
20d (null in current data) returns; MFE/MAE 5d/10d; stop-hit and target-hit simulation;
relative return vs SPY (vs QQQ / sector ETF marked unavailable); friction-adjusted return
(`raw − ROUND_TRIP_FRICTION_PCT`). Options theoretical P/L is `unavailable` (no historical
chain).

**Maturity:** an event is *mature* for the primary horizon if its 5d forward return is
present. Immature events are excluded from every pass/fail computation and counted
separately; the next maturity date is reported.

**Stop/target simulation honesty:** only MAE and MFE are known, not intraday ordering.
When both the stop and the target are touched in a window the order is unknown, so the
simulation conservatively assumes the **stop** filled first and flags the event ambiguous.
Stop/target hit-rates are reported as diagnostics, not as the headline expectancy.

## 7. Regime segmentation (Task 5)

Canonical buckets: `BULL_CONTINUATION, BULL_PULLBACK, FRAGILE, RISK_OFF, STRESS,
HIGH_VIX, LOW_VIX, SECTOR_BREADTH_BROAD, SECTOR_BREADTH_NARROW`. Buckets with no sample
are listed with `n=0` rather than dropped, so the bull-tape-only limitation is explicit.
Results are **never averaged blindly across regimes** — a family may only work in one.

## 8. Pass/fail gates (Task 6)

All metrics for the verdict are computed on the **clean epoch** (≥ 2026-05-08); the full
ledger is reported alongside for visibility. Gates (all evaluated, the verdict is derived
from them — not assigned by hand):

1. `min_mature_sample` — ≥ `MIN_MATURE_SAMPLE` (30) resolved-5d events.
2. `net_5d_expectancy_positive` and `net_10d_expectancy_positive` (after friction).
3. `beats_spy` — mean rel-SPY 5d > 0.
4. `beats_random_control` — net 5d expectancy > the random-liquid control's.
5. `beats_cash` — net 5d expectancy > 0 (cash baseline).
6. `mae_acceptable` — mean 5d MAE > −8%.
7. `stop_hit_rate_acceptable` — simulated stop-hit rate < 50%.
8. `not_one_outlier` — removing the single best event keeps net 5d expectancy > 0.
9. `risk_definable` — a stop can be defined and `heat_cap_fit` holds for the cohort.

**Verdict ladder:** `REJECT → NEED_MORE_DATA → WATCHLIST_RESEARCH →
READY_FOR_DEEPER_BACKTEST → READY_FOR_PAPER_SPEC`.

- `NEED_MORE_DATA` if sample gate fails (immature/too few).
- `REJECT` if sample adequate but net expectancy negative at both horizons.
- `WATCHLIST_RESEARCH` if adequate but mixed/marginal.
- `READY_FOR_DEEPER_BACKTEST` if it clears edge + control gates but is single-regime or
  carries a caution flag — earns a point-in-time backtest, **not** a paper spec.
- `READY_FOR_PAPER_SPEC` only if every core gate passes across more than one regime.

If no family qualifies: **"No strategy ready. Stay research-only."**

## 9. Options expression (Task 7)

`evaluate_options_expression(...)` is a pure function: it requires a **valid stock setup
first** and then chooses an expression, or rejects. Structures: `CALL_DEBIT_SPREAD`,
`PUT_CREDIT_SPREAD`, `CSP_WHEEL`, `NO_OPTIONS_EDGE`. Rejection on: no valid setup, wide
spread, unreliable IV/greeks, thin chain, extended entry, near-earnings binary risk, or
unresolved bearish hedge. CSP/Wheel additionally requires quality tier, acceptable
assignment, and sufficient buying power — if any is missing it is **not** chosen.
`max_loss / max_profit / breakeven / risk_reward` are emitted only when real strike +
premium inputs are supplied; from the historical spine they are `unavailable`.

## 10. Anti-label-fix rules (Task 9)

Enforced in code and reporting: immature events never count as winners; losers are never
excluded post-hoc; thresholds are fixed in-module (not tuned on this sample); no verdict
is upgraded by renaming; blocked candidates stay visible. Every family reports
`sample_size`, `caution_flag`, `biggest_weakness`, and `falsifier` (what would disprove
the thesis).

## 11. Surfacing (Task 11)

The MCP audit orchestrator surfaces a compact, cache-only block:

```
STRATEGY TOURNAMENT:
  best_candidate = <family or none>
  verdict = NEED_MORE_DATA / READY_FOR_PAPER_SPEC / REJECT / ...
  short_side = OFF / RESEARCH / TEST_CANDIDATE
  options_expression = NONE / RESEARCH_ONLY
```

Dashboard wiring is intentionally deferred (per "do not overbuild dashboard UI"); a
one-line cache-only string is available if the operator wants it later.

## 12. Outputs

- `cache/research/strategy_tournament_latest.json`
- `logs/strategy_tournament_latest.txt`
- `docs/research/STRATEGY_TOURNAMENT_RESULTS.md`
