# Scanner Truth Review — 2026-05 (Phase 1G.5)

*Generated 2026-08-01T00:34:40.965524+00:00 · 1G.5 — Scanner Truth Review (FULL: Tasks 1-11) · research-only, cache-only.*

## 1. Executive summary

- **Were market winners missed? YES.** Of **252** liquid winners ≥+80% (**172** ≥2x), only **0** ever touched any historized funnel stage → **winner recall 0.0%**.
- **They fell out before the council saw them.** The active long funnel (VOYAGER+SNIPER) logged only **4 distinct tickers**; veto_log is ~all SHORT. `UNIVERSE_MISS` dominates.
- **A dumb baseline beats the funnel on recall:** simple 20d-RS recall **43.1%** vs funnel **0.0%** on the same forward set.
- **A clean entry existed:** 4.0% of winners had a buyable window (median 14.0d) before becoming extended — the system simply never surfaced them.
- Consistent with design (Voyager buyable-pullback mandate; Alpha penalises large momentum leaders). The question is whether a **research-only momentum/RS recall lane** + **theme radar** is worth adding — validated forward, never curve-fit.

## 2. Were market winners missed?

- Liquid winners ≥+50%: **476**, ≥+80%: **252**, ≥2x: **172** (scanned 5683; 640 illiquid excluded).
- By theme: `other`=225, `biotech_healthcare`=97, `semiconductors`=60, `unknown`=47, `hardware`=25, `space_aerospace`=14, `memory_storage`=3, `quantum`=3, `nuclear_energy`=1, `crypto_blockchain`=1.

## 3. Top missed winners (liquid, by trailing max return)

| ticker | theme | max ret | $vol(M) | recall |
|---|---|--:|--:|:--:|
| AGL | biotech_healthcare | 1080% | 34 | — |
| MXL | semiconductors | 647% | 324 | — |
| CAR | other | 566% | 152 | — |
| FCEL | other | 437% | 256 | — |
| BLZE | other | 388% | 30 | — |
| MRAM | semiconductors | 383% | 17 | — |
| BAND | other | 375% | 84 | — |
| CUE | biotech_healthcare | 360% | 5 | — |
| PENG | hardware | 355% | 335 | — |
| FIRY | other | 354% | 12 | — |
| WOLF | semiconductors | 343% | 845 | — |
| EVC | other | 333% | 17 | — |
| APPS | other | 324% | 26 | — |
| QTTB | biotech_healthcare | 316% | 49 | — |
| REPL | biotech_healthcare | 305% | 116 | — |
| ABSI | biotech_healthcare | 297% | 35 | — |
| ALAB | semiconductors | 290% | 1478 | — |
| SHAZ | other | 287% | 149 | — |
| BB | other | 286% | 201 | — |
| VSH | semiconductors | 272% | 225 | — |
| AMBQ | semiconductors | 257% | 39 | — |
| VPG | hardware | 256% | 52 | — |
| BRUN | other | 254% | 41 | — |
| MEI | hardware | 253% | 10 | — |
| CORT | biotech_healthcare | 252% | 152 | — |

## 4. Where they fell out of the funnel

| root cause | count |
|---|--:|
| FILTER_TOO_STRICT | 130 |
| UNIVERSE_MISS | 104 |
| DATA_MISS | 18 |

Detection timing: early **0**, late **0**, blind **252**.

## 5. Recall / precision metrics

- Recall (ever in funnel): **0.0%**; ≥2x bucket **0.0%**.
- Semiconductors recall **0.0%** (n=46).
- Recall-before-move **NOT_RETAINED**; forward precision **NOT_COMPUTABLE_YET**.

## 6. Comparison vs simple baselines

As-of 2026-05-05, 60td forward, 167 forward winners in 2340 liquid names.

| baseline | flagged | recall | precision | avg fwd ret |
|---|--:|--:|--:|--:|
| rs_20d | 383 | 43.1% | 18.8% | -1% |
| high_50d_breakout | 389 | 23.4% | 10.0% | 1% |
| vol_strength | 130 | 18.6% | 23.8% | 2% |
| sector_rs | 474 | 37.7% | 13.3% | -2% |
| mom_20_60 | 342 | 35.3% | 17.3% | -1% |

**Verdict:** a SIMPLE baseline ('rs_20d', recall 43.1%) caught more forward winners than the live funnel (0.0%). Sophistication did not buy recall here.

## 7. Theme / sector leadership audit

| theme | winners | median max | on board | seen | visibility |
|---|--:|--:|--:|--:|---|
| other | 225 | 78% | 0 | 0 | absent_from_board |
| biotech_healthcare | 97 | 84% | 0 | 0 | absent_from_board |
| semiconductors | 60 | 121% | 0 | 0 | absent_from_board |
| unknown | 47 | 71% | 0 | 0 | absent_from_board |
| hardware | 25 | 94% | 0 | 0 | absent_from_board |
| space_aerospace | 14 | 78% | 0 | 0 | absent_from_board |
| memory_storage | 3 | 200% | 0 | 0 | absent_from_board |
| quantum | 3 | 85% | 0 | 0 | absent_from_board |
| nuclear_energy | 1 | 120% | 0 | 0 | absent_from_board |
| crypto_blockchain | 1 | 88% | 0 | 0 | absent_from_board |

*Limitation:* FMP industry taxonomy is coarse: memory & AI-hardware mostly read 'Semiconductors'/'Hardware, Equipment & Parts' and cannot be cleanly separated by profile. Theme counts are lower bounds for those clusters.

**Theme Leadership Radar:** PROPOSED — research-only, not built this phase — A nightly cache-only report that clusters the liquid universe by trailing relative-strength co-movement (correlation of 20/60d returns) to detect emerging strength CLUSTERS independent of the coarse FMP industry labels, and surfaces the top leaders per cluster.

## 8. Filter audit

| filter | threshold | winners rej | losers rej | recall cost | verdict |
|---|---|--:|--:|--:|---|
| liquidity_price | price∈[$5,$1000] | 239 | 1321 | 50.3% | BY-DESIGN exclusion |
| liquidity_dvol | avg$vol≥$5M & vol≥300k | 279 | 2834 | 58.7% | BY-DESIGN exclusion |
| voyager_max_extension_ma50 | >12% above MA50 → reject | 144 | 879 | 30.3% | REVIEW |
| voyager_ma200_floor | price < MA200×0.92 → reject | 75 | 233 | 15.8% | KEEP |
| voyager_bars_needed_260 | <260 bars → reject | 252 | 3993 | 53.1% | INDETERMINATE |
| sniper_bars_needed_75 | <75 bars → reject | 26 | 203 | 5.5% | INDETERMINATE |
| alpha_market_cap_band | mcap∉[$300M,$80B] | 55 | 256 | 11.6% | KEEP |

_Not reliably computable (disclosed, not guessed):_ voyager_rs_130 / fundamental_score; voyager_dvol_trend_ratio; sniper_vol_spike_1.4x / atr_contraction_0.85; earnings_safe_days; options_liquidity / 13F_sponsorship; top_25_board_cap.

## 9. Entry-state timing audit

- **4.0%** of winners had a clean buyable window before becoming extended (median **14.0 days**); funnel detected **0**.
- buyable = near MA50 (−8%..+12%), above MA200 floor where computable. A buyable window existing but no detection ⇒ the system had a clean early entry it did not take (ENTRY_VALIDATOR/UNIVERSE gap).

## 10. Recommendations (evidence-based)

### R1_historize_funnel — Historize the per-ticker funnel (lens + gatekeeper)
- **Evidence:** Recall-before-move and forward-precision are uncomputable; only ~6 days of board history exist.
- **Benefit:** Makes the NEXT autopsy faithful; enables forward precision.
- **Risk:** None (cache-only, additive).
- **Complexity:** LOW — funnel_historizer.py shipped; add a daily timer. · **Scope:** research-only · **Overfit risk:** none

### R2_investigate_emission_gap — Investigate why the LONG funnel surfaced only 4 tickers to the council
- **Evidence:** Winner recall 0.0%; veto_log has 27 distinct tickers total, ~all SHORT. The miss is upstream of the council, not a threshold-tuning issue.
- **Benefit:** Biggest single lever on recall — likely a universe-seed or scan-emission/score-gate gap.
- **Risk:** Investigation only; no change until understood.
- **Complexity:** MEDIUM — needs the historizer + a few weeks of universe snapshots. · **Scope:** research-only · **Overfit risk:** none

### R3_deepen_price_cache — Deepen the price-history cache beyond ~110 bars
- **Evidence:** 19/252 winners are cache-limited; Voyager's 260-bar gates can't be reconstructed or back-tested faithfully.
- **Benefit:** Faithful PIT reconstruction and back-tests; better universe coverage.
- **Risk:** Provider-budget cost for backfill (one-time).
- **Complexity:** LOW-MEDIUM. · **Scope:** research-only (cache) · **Overfit risk:** none

### R4_momentum_RS_baseline_lane — Evaluate a regime-adaptive momentum/RS recall lane (research-only first)
- **Evidence:** A simple baseline ('rs_20d', recall 43.1%) caught far more forward winners than the funnel (0.0%). 54.6% of winners had a clean buyable window the system never took.
- **Benefit:** Materially higher recall on momentum leaders.
- **Risk:** Low precision (8-12%) — a recall lane needs a precision gate before any promotion; momentum drawdowns are real.
- **Complexity:** MEDIUM. · **Scope:** research-only · **Overfit risk:** MEDIUM — validate forward, do NOT tune to this winner set.

### R5_theme_leadership_radar — Build a research-only Theme Leadership Radar
- **Evidence:** Semiconductors: 60 winners, median max return ~121%, recall ~nil; coarse FMP labels hide memory/AI-hardware clusters.
- **Benefit:** Early detection of strength clusters; an additive Alpha feature later.
- **Risk:** Descriptive only; scoring use needs a forward gate.
- **Complexity:** MEDIUM. · **Scope:** research-only · **Overfit risk:** MEDIUM

### R6_quantify_liquidity_opportunity_cost — Quantify (do not remove) the penny/illiquid exclusion opportunity cost
- **Evidence:** liquidity filters reject 55-65% of winners — but those are sub-$5/illiquid names with real tradability risk.
- **Benefit:** Informed decision on whether a small, ring-fenced low-price sleeve is worth it.
- **Risk:** Trading illiquid names has slippage/borrow risk.
- **Complexity:** LOW. · **Scope:** research-only · **Overfit risk:** low

### R0_do_not_tune — Do NOT tune thresholds to recapture past winners
- **Evidence:** The whole audit set is realized; tuning to it is curve-fitting.
- **Benefit:** Avoids overfit / false confidence.
- **Risk:** n/a
- **Complexity:** n/a · **Scope:** discipline · **Overfit risk:** n/a

## 11. What NOT to change yet

- No threshold tuning to recapture past winners (curve-fitting).
- No execution / governance / strategy-registry / live-capital / paper-signal changes.
- No new live strategy. Any momentum/RS lane or theme radar stays research-only behind a forward-validation gate.

## 12. Recommended next phase

1. Add a daily `funnel_historizer` timer; accrue ~4-6 weeks of dated boards+lens+gatekeeper.
2. Backfill deeper price history; re-run this review to lift the cache-limited caveats.
3. Stand up the research-only momentum/RS recall lane + Theme Leadership Radar and measure FORWARD recall/precision on out-of-sample winners before any promotion decision.

---
*Fidelity disclosures:*
> - Winner recall traces the COUNCIL FUNNEL DB stages (scan_results / veto_log / decisions), all frozen since the 2026-06-13 decommission — the headline recall decays toward 0% mechanically as winner windows pass the last DB writes and does NOT measure the live research board (verified 2026-07-16: the research watchlist history caught 137/237 ≥80% winners at least once). Live-board recall is the prospective scanner-recall cohorts tracker.
> - Alpha board+overlay are historized via research_delta since ~2026-05-20 (~6 days); per-ticker Stock Lens + Gatekeeper were NOT — funnel_historizer.py now closes that gap for FUTURE autopsies.
> - Voyager 260-bar gates are indeterminate for ~110-bar cache names (19/252); not attributed to the scanner. The bars_needed filter audit is likewise marked INDETERMINATE (cache-confounded).
> - Forward precision is NOT_COMPUTABLE_YET (today's board has no forward window).
> - Root causes leaning on non-historized stages carry an _INFERRED suffix.
> - Theme classifier is profile-text-limited; memory/AI-hardware counts are lower bounds.

