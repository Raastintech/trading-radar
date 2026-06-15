# Strategy Truth Review — May 2026

**Date:** 2026-05-24
**Status:** Audit only. No code, governance, registry, threshold, or DB changes were made.
**Sidecars:** `cache/research/strategy_truth_review_latest.json`, `logs/strategy_truth_review_latest.txt`
**Sources:** `db/trading.db` (decisions, paper_signals, voyager_paper_signals, veto_log, paper_signal_outcomes), `cache/research/regime_forecast_latest.json` (built 2026-05-23), `cache/research/alpha_discovery_board_latest.json` (built 2026-05-23), `cache/research/forecast_forward_summary_latest.json`.

> **Honesty note on sample size.** The operational ledgers are only ~32 days deep (`paper_signals` from 2026-04-22; `decisions` 2026-04-23 → 2026-05-15). Every realized-PnL number below is a single-digit or low-double-digit sample. **Nothing here is statistically significant.** This review is about *posture, alignment, and hygiene*, not about declaring any sleeve good or bad on returns.

---

## 0. Headline (read this first)

1. **The system did nothing useful for 7 days while the market rallied.** Zero strategy-opened positions in the last 7d; in fact **zero `decisions` rows were logged at all** in the last 7d. Over the same window SPY +0.9%/5d (+4.47%/20d), QQQ +1.25%/5d (+8.12%/20d), IWM +2.71%/5d, VIX 16.7 — a textbook low-vol bull continuation.
2. **The only sleeve that fired was the one the system's own forecaster says to avoid.** `SHORT_A` emitted 390 signals in 7d (all governance-blocked). The forecaster rates `SHORT_A` = **avoid** ("broad bid is hostile to short setups") and `VOYAGER` = **favored**. VOYAGER produced **0** signals.
3. **The 390 blocked SHORT signals are not 390 opportunities.** They are re-emissions of 1–3 tickers, logged ~78×/day, blocked 87% by "at max paper positions (2)" and 13% by "already has active paper exposure". This is a scanner/logging-hygiene defect, not alpha being rejected.
4. **No sleeve has clean, profitable paper evidence.** SNIPER is +14.9% on **n=1**. SHORT_A is net-negative with -18% to -20% short-squeeze tails. VOYAGER's 3-trade sample is dragged to -4.6% by one -17.3% blowup. **Edge is unproven across the board.**
5. **The best-leveraged next move is LEADER_RESET.** Alpha Discovery *already* produces a "Liquid Leadership Reset" track with leaders, extension state, and entry-timing labels. The missing piece is exactly what the prompt suspected: a disciplined **entry trigger + risk sizing + paper logging** sleeve to consume it.

---

## 1. Per-sleeve truth review

### SNIPER (SNIPER_V6) — active paper

| Field | Value |
|---|---|
| Thesis | Tactical post-event / breakout long sniping. |
| Status | `active_paper`, paper-ledger sleeve. |
| Signals 7d / 30d | 0 / 7 |
| Council 30d | 1 approved, 0 vetoed |
| Governance-blocked 30d | 6 (all "PANW already has active paper exposure") |
| Opened / closed (all-time) | 1 / 1 |
| Realized | **n=1: PANW +14.9%** |
| Main rejection reason | Duplicate exposure — same ticker re-emitted every loop while already held. |
| Regime alignment | Forecaster = **allowed** (neutral context). |
| Institutional-edge thesis | Partial — event/breakout edge is real, but one trade proves nothing. |
| **Verdict** | **KEEP PAPER** |

The single positive trade is encouraging but meaningless statistically. SNIPER's real problem is **frequency**: one trade in a month is too thin to validate anything before the 2026-12-01 holdout. Keep it logging untouched; do not tune it to fire more.

### VOYAGER — active paper

| Field | Value |
|---|---|
| Thesis | Long-horizon institutional-sponsorship leadership longs (13F + RS + trend). |
| Status | `active_paper`, legacy `voyager_paper_signals` table. |
| Signals 7d / 30d | 0 / 2 (3 ever) |
| Council 30d | 64 approved, 37 vetoed |
| Opened / closed (all-time) | 3 / 3 |
| Realized | **n=3: CRS +1.7%, CRS +1.9%, VOYA -17.3%** |
| Main rejection reason | Not council rejection — the council *approves* VOYAGER 64×; almost nothing converts into a logged signal/position. |
| Regime alignment | Forecaster = **FAVORED** ("broad trend healthy, breadth supportive, vol contained"). |
| Institutional-edge thesis | Yes — this *is* the institutional-leadership thesis. |
| **Verdict** | **REDESIGN** |

This is the most damning finding. VOYAGER is the sleeve **best aligned with both our institutional-edge thesis and the current regime**, the council approved it 64 times in 30 days, and it produced **two signals and zero new positions**. The 64-approval → 2-signal collapse means the bottleneck is downstream of the council (entry construction / sizing / paper governance), not selection. One -17.3% loss (VOYA) dominates the entire realized record. VOYAGER doesn't need new thresholds — it needs its **approval→signal→position conversion path rebuilt** so a favored regime actually produces longs.

### SHORT_A — active paper

| Field | Value |
|---|---|
| Thesis | Earnings-event continuation shorts (post-gap-down momentum). |
| Status | `active_paper`, paper-ledger sleeve. |
| Signals 7d / 30d / all-time | 390 / 3,325 / 3,529 |
| Distinct tickers (all-time) | **29** (CHTR 234×, CRK 230×, BTU 230×, PAHC 229× …) |
| Council 30d | 621 approved, 449 vetoed |
| Governance-blocked (all-time) | 3,524 — **3,064 "at max positions (2)" + 460 "duplicate exposure"** |
| Opened / closed (all-time) | 15 / 15 |
| Realized | **n=15: 7 wins / 8 losses, net negative (avg -2.1%).** Squeeze tails: TSCO -18.6%, TSCO -20.6%, PH -10.0%. |
| Main rejection reason | Position-count cap (2) + duplicate exposure — i.e. re-emission noise, not distinct edges. |
| Regime alignment | Forecaster = **AVOID** ("broad bid is hostile to short setups"). |
| Institutional-edge thesis | No — event-continuation shorting is not a durable institutional edge and is fighting the tape. |
| **Verdict** | **FREEZE** (to research-only) |

Four independent reasons converge on freeze: (a) realized net-negative; (b) **asymmetric squeeze risk** — losers are 4–10× the size of winners; (c) it is fighting a low-VIX bull continuation the forecaster explicitly flags as hostile; (d) it is the source of essentially all the ledger/dashboard noise — **99.6% of its 3,529 signals are re-logged blocked duplicates** of 29 tickers. The "390 blocked in 7d" headline is not opportunity loss; it is the same handful of setups being re-stamped ~78×/day against a 2-slot cap with no cooldown/dedup. Freezing it removes the noise without touching governance and without forcing any trades.

> Note on the "sizing too large vs heat cap" hypothesis from the brief: the data shows the dominant block is **position-count cap (2), not heat/sizing.** Only 5 SHORT signals ever carried a non-zero `allocation_pct`. Sizing is a secondary symptom; the primary defect is **no de-duplication / re-emission control** in the SHORT scanner.

### REMORA / CONTRARIAN / PATHFINDER — frozen / future-research

All three: 0 signals 7d/30d, 0 positions, no evidence. `REMORA` and `CONTRARIAN` are `frozen`; `PATHFINDER` is `future_research`/paused. **Verdict: RESEARCH ONLY — no change.** They are correctly dormant; the "one strategy in deep validation at a time" doctrine says they stay frozen while we work the active set.

### Alpha Discovery — research engine (not a sleeve)

| Field | Value |
|---|---|
| Role | Research/discovery engine; does not open positions. |
| Built | 2026-05-23, nightly mode, 20 items. |
| Tracks | **Liquid Leadership Reset: 10**, Emerging Opportunity: 10. |
| Buckets | Too Late / Crowded: 11, Early Discovery: 9. |
| Tiers | A: 15, B: 1, C: 4. |
| Stance | **selective — "favor pullback candidates, avoid chasing extended names."** |
| Per-item entry vocab | `entry_state` ∈ {`too extended`, `watch reclaim`, `watch only`}; plus `entry_validator_state`, `options_quality`, `crowd_penalty`, `sponsorship_score`, `liquidity_score`, `validator_state`, `why_now`/`why_not`. |
| **Verdict** | **KEEP — strongest asset in the stack.** |

This is the most important positive finding in the review. Alpha Discovery is already doing the hard 80% of a leadership-pullback sleeve: it finds liquid leaders, scores sponsorship/crowding, measures extension, and classifies entry readiness with a vocabulary that maps almost 1:1 onto the LEADER_RESET labels we want. **It has no downstream sleeve consuming it.** That is the gap to close.

---

## 2. Opportunity-cost review (7d / 14d / 30d)

| Benchmark | 5d | 10d | 20d | > 50d MA | > 200d MA |
|---|---|---|---|---|---|
| SPY | +0.90% | +1.12% | +4.47% | yes | yes |
| QQQ | +1.25% | +0.92% | +8.12% | yes | no |
| IWM | +2.71% | +0.33% | +3.06% | yes | yes |
| VIX | 16.7 (20d avg 17.8, falling) | | | | |

System behavior over the same window: **0 new positions opened in 7d, 0 decisions logged in 7d**, and the only active emitter was the AVOID-rated short sleeve.

- **Did the system miss a broad bull move?** **Yes.** Broad indices up, breadth constructive, VIX low and falling. The system was flat.
- **Was the silence justified by risk rules?** **No.** The forecaster classified the regime as Bull Continuation (55%), constructive bias, defensive_mass 0.0. There was no risk-off / stress signal to justify standing down. (Caveat: forecaster confidence is "low" and an invalidation flag tripped on narrow leadership — leading sectors 1<2 — so *some* caution was defensible, but flat-and-short was not.)
- **Are the strategies too conservative?** **On the long side, yes** — the favored long sleeves are effectively dormant. On the short side they are the opposite of conservative (loud) but blocked.
- **Noisy but blocked?** **Yes, on the short side.** 390 blocked SHORT signals/7d are re-emission noise, not 390 opportunities.
- **Protecting capital, or failing to find edge?** **Failing to find long edge in a favorable long regime.** It is not principled capital protection; it is a structural misalignment — the favored sleeve is silent, the avoid sleeve is loud, and the discovery engine that finds longs has nothing consuming it.

### Forward-outcome tracking gap (the brief's last concern)

`forecast_forward_summary_latest.json`: **58 snapshots logged, 0 matured**, `by_regime` empty, sector basket `n=0`. `paper_signal_outcomes` holds only ~19 rows total. So the forward-tracking machinery exists and is accumulating, but **it has produced no resolved forward evidence yet**. Any sleeve verdict on returns is therefore provisional. Strengthening the resolver cadence (so snapshots actually mature into 5d/10d/20d outcomes) is a prerequisite for trusting future reviews — and is built into the LEADER_RESET validation plan.

---

## 3. Best next sleeve — ranking

Evaluated against: simplicity · data availability · testability on the current stack · institutional-edge alignment · expected signal frequency · risk-control clarity · probability of clean paper evidence in 30–60 days. Scores are 1–5 (5 best), informal.

| Rank | Candidate | Simpl. | Data | Testable now | Edge fit | Freq | Risk clarity | 30–60d evidence | Notes |
|---|---|---|---|---|---|---|---|---|---|
| **1** | **LEADER_RESET / Institutional Pullback Reclaim** | 4 | **5** | **5** | **5** | 4 | 5 | **5** | Alpha Discovery already supplies leaders + extension + entry-state. Missing piece = trigger/sizing/logging only. Long-biased, fits the regime. |
| 2 | Redesigned VOYAGER | 3 | 5 | 4 | 5 | 3 | 4 | 3 | Same edge family as #1 but heavier (13F, long horizon) and its conversion path is already broken. LEADER_RESET is the cleaner expression of the same thesis. |
| 3 | EARNINGS_DRIFT / PEAD | 4 | 4 | 4 | 4 | 4 | 4 | 4 | Well-documented anomaly, FMP earnings calendar already cached. Strong #2 if LEADER_RESET stalls. Needs careful event windows. |
| 4 | OPTIONS_FLOW | 2 | 3 | 3 | 4 | 3 | 3 | 2 | Tradier IV/greeks just came online (2026-05-21); OI/greeks coverage still partial. Promising but data is youngest and noisiest. |
| 5 | 13F_EMERGING | 2 | 3 | 3 | 4 | 2 | 3 | 2 | Quarterly cadence ⇒ low frequency ⇒ slow to accumulate paper evidence; overlaps VOYAGER. |
| 6 | Redesigned SHORT_A | 2 | 4 | 3 | 2 | 4 | 2 | 2 | Fighting the regime; squeeze tails; not an institutional edge. Park as research. |
| 7 | CRISIS_FADE | 2 | 4 | 2 | 3 | 1 | 2 | 1 | Regime-dependent; in a calm tape it would essentially never fire ⇒ no evidence in 60d. |

**LEADER_RESET wins decisively** on the two criteria that matter most right now: *testability on the current stack* and *probability of clean paper evidence in 30–60 days*. It is the only candidate where the expensive infrastructure (discovery, extension scoring, entry-state classification, options enrichment, gatekeeper, entry validator, risk telemetry) is **already built and already running** — it just needs a disciplined sleeve to consume it.

---

## 4. LEADER_RESET v0 thesis (summary; full spec in `LEADER_RESET_V0_SPEC.md`)

> **Buy institutional-strength leaders after a controlled pullback and reclaim — not during extension.**

The system's repeated failure mode is being either flat or short while leaders run. LEADER_RESET inverts that: it waits for an already-validated leader (from Alpha Discovery's Liquid Leadership Reset track) to *cool off* to its EMA20/VWAP on contracting volume, then logs a paper signal only on a *reclaim* trigger, sized to a hard risk cap, gated by regime and the existing gatekeeper/entry-validator. It emits **research labels, never "BUY NOW"**: `RESEARCH_READY`, `WATCH_RECLAIM`, `LATE_EXTENDED`, `BLOCKED`, `NO_EDGE` — which map directly onto Alpha Discovery's existing `entry_state` vocabulary.

---

## 5. Why LEADER_RESET is better than the current sleeves

| Dimension | SHORT_A (freeze) | VOYAGER (redesign) | SNIPER (keep, thin) | **LEADER_RESET v0** |
|---|---|---|---|---|
| Regime fit (now) | Hostile (avoid) | Favored but silent | Allowed | **Favored, long-biased** |
| Institutional edge | Weak | Strong | Moderate | **Strong (leadership + sponsorship)** |
| Infra reuse | — | partial | partial | **High — consumes existing Alpha Discovery** |
| Risk shape | Asymmetric squeeze tails | One blowup dominates | OK | **Defined ATR/swing stop, capped R** |
| Signal hygiene | 99.6% dup noise | near-silent | dup re-emission | **dedup + cooldown by design** |
| Path to clean evidence | net-negative | dormant | n=1 | **highest in 30–60d** |

LEADER_RESET is not a new bet — it is the **disciplined entry layer the institutional-leadership thesis has been missing**. It reuses the strongest component (Alpha Discovery), expresses the regime-favored direction (long leaders), and is engineered from day one with the hygiene controls SHORT_A lacks.

---

## 6. Data needed to test it

All already present in the stack (detailed in `LEADER_RESET_VALIDATION_PLAN.md`):
- **Daily OHLCV** (Alpaca SIP, cached `cache/prices/*.parquet`) for EMA20/50/200, VWAP proxy, ATR, RS, extension, volume contraction.
- **Alpha Discovery board** (`alpha_discovery_board_latest.json`) — leadership universe + extension/entry-state/crowd fields.
- **Regime forecast** (`regime_forecast_latest.json`) — SPY>50d/200d, VIX, regime state, strategy favorability.
- **FMP earnings calendar** (cached 6h) — earnings-proximity exclusion.
- **Options enrichment** (Alpaca + Tradier) — `options_quality` confirm/neutral/bearish-hedge filter.
- **Gatekeeper + entry-validator artifacts** — BLOCK/clean gating.
- **Risk telemetry** (slippage/concentration/shadow-sizing) — spread/slippage feasibility + heat-cap pre-check.

No new provider integrations required. The only gap is **historical reconstruction of the Alpha Discovery / entry-state fields** for backtest event-construction without lookahead — addressed in the validation plan.

---

## 7. Risks and assumptions

- **Sample-size humility.** All current verdicts rest on ≤15-trade samples. Treat them as posture calls, not performance facts.
- **Lookahead in backtest.** Alpha Discovery scores are computed with current data; reconstructing historical leadership/extension/entry-state without leaking forward information is the hardest part of validation. Mitigated by point-in-time reconstruction from raw OHLCV + as-of fundamentals (see plan).
- **Regime dependence.** LEADER_RESET is a long sleeve; it will (correctly) go quiet in risk-off/stress. That is a feature, but it means evidence accrues only in constructive/pullback regimes — calendar luck affects the 60-day window.
- **"Reclaim" definition risk.** Too loose ⇒ chasing; too strict ⇒ SNIPER's frequency problem. v0 picks a concrete, testable definition (prior-day-high or EMA20/VWAP reclaim with volume confirmation) precisely so it can be falsified.
- **Forward-outcome resolver must work.** With 0 of 58 snapshots matured today, the sleeve's evidence depends on fixing resolver cadence first.
- **Don't repeat SHORT_A's noise.** v0 mandates per-ticker dedup + cooldown and a heat-cap pre-check *before* logging a paper signal.
- **No tuning-to-fit.** Per the brief and doctrine, we do not loosen current thresholds to manufacture trades, and we do not retrofit old sleeves to look profitable.

---

## 8. Recommended next phase

1. **Hygiene + truth first (this doc).** Surface verdicts; **FREEZE SHORT_A** to stop ledger/dashboard noise (registry change deferred — *not done in this audit*; recommended as the first action of the next build phase).
2. **Fix forward-outcome resolution.** Get the 58 logged regime snapshots and paper-signal outcomes maturing on cadence so future reviews have real forward evidence. Prerequisite for trusting any sleeve.
3. **Backtest LEADER_RESET v0** per `LEADER_RESET_VALIDATION_PLAN.md` against the pass/fail gates. No paper sleeve until gates pass.
4. **If gates pass → paper-only LEADER_RESET sleeve** (research labels only, dedup + cooldown + heat-cap pre-check, no execution/governance/registry coupling) for 30–60 days of clean evidence.
5. **VOYAGER conversion-path redesign** in parallel-but-after (one sleeve in deep validation at a time): diagnose why 64 council approvals yield ~2 signals.
6. **Re-run this truth review monthly** as a standing artifact.

**Not done in this phase (by design):** no live trading, no execution/OrderManager/governance changes, no threshold tuning, no registry edits, no trade proposals, no DB mutation, no historical-evidence rewrite.
