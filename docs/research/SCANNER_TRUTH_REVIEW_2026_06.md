# Scanner Truth Review — 2026-05 (Phase 1G.5)

*Generated 2026-06-19T00:34:09.237590+00:00 · 1G.5 — Scanner Truth Review (FULL: Tasks 1-11) · research-only, cache-only.*

## 1. Executive summary

- **Were market winners missed? YES.** Of **246** liquid winners ≥+80% (**158** ≥2x), only **5** ever touched any historized funnel stage → **winner recall 2.0%**.
- **They fell out before the council saw them.** The active long funnel (VOYAGER+SNIPER) logged only **4 distinct tickers**; veto_log is ~all SHORT. `UNIVERSE_MISS` dominates.
- **A dumb baseline beats the funnel on recall:** simple 20d-RS recall **22.6%** vs funnel **1.8%** on the same forward set.
- **A clean entry existed:** 36.2% of winners had a buyable window (median 26d) before becoming extended — the system simply never surfaced them.
- Consistent with design (Voyager buyable-pullback mandate; Alpha penalises large momentum leaders). The question is whether a **research-only momentum/RS recall lane** + **theme radar** is worth adding — validated forward, never curve-fit.

## 2. Were market winners missed?

- Liquid winners ≥+50%: **494**, ≥+80%: **246**, ≥2x: **158** (scanned 5548; 800 illiquid excluded).
- By theme: `other`=218, `unknown`=102, `biotech_healthcare`=60, `semiconductors`=59, `hardware`=31, `space_aerospace`=15, `quantum`=3, `memory_storage`=3, `nuclear_energy`=2, `crypto_blockchain`=1.

## 3. Top missed winners (liquid, by trailing max return)

| ticker | theme | max ret | $vol(M) | recall |
|---|---|--:|--:|:--:|
| INHD | other | 2825% | 551 | — |
| ASTC | space_aerospace | 2056% | 316 | — |
| RXT | unknown | 1650% | 71 | — |
| VCIG | other | 1089% | 29 | — |
| STI | other | 789% | 218 | — |
| AGL | biotech_healthcare | 707% | 26 | — |
| SDOT | other | 691% | 37 | — |
| CAR | other | 508% | 167 | — |
| MXL | semiconductors | 480% | 693 | — |
| LWLG | other | 444% | 88 | — |
| PIII | biotech_healthcare | 441% | 5 | — |
| CODX | biotech_healthcare | 439% | 437 | — |
| AKTX | biotech_healthcare | 433% | 73 | — |
| AXTI | semiconductors | 407% | 1016 | — |
| WOLF | semiconductors | 394% | 831 | — |
| SKLZ | other | 386% | 7 | — |
| ATOM | semiconductors | 381% | 42 | — |
| MRAM | semiconductors | 374% | 238 | — |
| AAOI | semiconductors | 366% | 1991 | — |
| BAND | other | 362% | 51 | — |
| MNTS | space_aerospace | 340% | 192 | — |
| REPL | biotech_healthcare | 340% | 49 | — |
| OCC | other | 326% | 30 | — |
| CRCA | unknown | 318% | 66 | — |
| AEHR | semiconductors | 298% | 285 | — |

## 4. Where they fell out of the funnel

| root cause | count |
|---|--:|
| FILTER_TOO_STRICT | 106 |
| UNIVERSE_MISS | 99 |
| DATA_MISS | 40 |
| VALID_NO_TRADE | 1 |

Detection timing: early **0**, late **5**, blind **241**.

## 5. Recall / precision metrics

- Recall (ever in funnel): **2.0%**; ≥2x bucket **2.5%**.
- Semiconductors recall **5.0%** (n=40).
- Recall-before-move **NOT_RETAINED**; forward precision **NOT_COMPUTABLE_YET**.

## 6. Comparison vs simple baselines

As-of 2026-03-24, 60td forward, 283 forward winners in 2313 liquid names.

| baseline | flagged | recall | precision | avg fwd ret |
|---|--:|--:|--:|--:|
| rs_20d | 400 | 22.6% | 16.0% | 11% |
| high_50d_breakout | 151 | 5.3% | 9.9% | 6% |
| vol_strength | 118 | 4.2% | 10.2% | 1% |
| sector_rs | 388 | 21.9% | 16.0% | 11% |
| mom_20_60 | 43 | 7.8% | 51.2% | 37% |

**Verdict:** a SIMPLE baseline ('rs_20d', recall 22.6%) caught more forward winners than the live funnel (1.8%). Sophistication did not buy recall here.

## 7. Theme / sector leadership audit

| theme | winners | median max | on board | seen | visibility |
|---|--:|--:|--:|--:|---|
| other | 218 | 76% | 14 | 1 | visible_on_board |
| unknown | 102 | 70% | 0 | 0 | absent_from_board |
| biotech_healthcare | 60 | 81% | 4 | 2 | visible_on_board |
| semiconductors | 59 | 105% | 2 | 2 | visible_on_board |
| hardware | 31 | 94% | 0 | 0 | absent_from_board |
| space_aerospace | 15 | 107% | 0 | 0 | absent_from_board |
| quantum | 3 | 89% | 0 | 0 | absent_from_board |
| memory_storage | 3 | 134% | 0 | 0 | absent_from_board |
| nuclear_energy | 2 | 79% | 0 | 0 | absent_from_board |
| crypto_blockchain | 1 | 96% | 0 | 0 | absent_from_board |

*Limitation:* FMP industry taxonomy is coarse: memory & AI-hardware mostly read 'Semiconductors'/'Hardware, Equipment & Parts' and cannot be cleanly separated by profile. Theme counts are lower bounds for those clusters.

**Theme Leadership Radar:** PROPOSED — research-only, not built this phase — A nightly cache-only report that clusters the liquid universe by trailing relative-strength co-movement (correlation of 20/60d returns) to detect emerging strength CLUSTERS independent of the coarse FMP industry labels, and surfaces the top leaders per cluster.

## 8. Filter audit

| filter | threshold | winners rej | losers rej | recall cost | verdict |
|---|---|--:|--:|--:|---|
| liquidity_price | price∈[$5,$1000] | 464 | 1121 | 51.6% | BY-DESIGN exclusion |
| liquidity_dvol | avg$vol≥$5M & vol≥300k | 550 | 2374 | 61.2% | BY-DESIGN exclusion |
| voyager_max_extension_ma50 | >12% above MA50 → reject | 83 | 363 | 9.2% | KEEP |
| voyager_ma200_floor | price < MA200×0.92 → reject | 53 | 77 | 5.9% | KEEP |
| voyager_bars_needed_260 | <260 bars → reject | 766 | 4526 | 85.2% | INDETERMINATE |
| sniper_bars_needed_75 | <75 bars → reject | 730 | 4306 | 81.2% | INDETERMINATE |
| alpha_market_cap_band | mcap∉[$300M,$80B] | 61 | 183 | 6.8% | KEEP |

_Not reliably computable (disclosed, not guessed):_ voyager_rs_130 / fundamental_score; voyager_dvol_trend_ratio; sniper_vol_spike_1.4x / atr_contraction_0.85; earnings_safe_days; options_liquidity / 13F_sponsorship; top_25_board_cap.

## 9. Entry-state timing audit

- **36.2%** of winners had a clean buyable window before becoming extended (median **26 days**); funnel detected **0**.
- buyable = near MA50 (−8%..+12%), above MA200 floor where computable. A buyable window existing but no detection ⇒ the system had a clean early entry it did not take (ENTRY_VALIDATOR/UNIVERSE gap).

## 10. Recommendations (evidence-based)

### R1_historize_funnel — Historize the per-ticker funnel (lens + gatekeeper)
- **Evidence:** Recall-before-move and forward-precision are uncomputable; only ~6 days of board history exist.
- **Benefit:** Makes the NEXT autopsy faithful; enables forward precision.
- **Risk:** None (cache-only, additive).
- **Complexity:** LOW — funnel_historizer.py shipped; add a daily timer. · **Scope:** research-only · **Overfit risk:** none

### R2_investigate_emission_gap — Investigate why the LONG funnel surfaced only 4 tickers to the council
- **Evidence:** Winner recall 2.0%; veto_log has 27 distinct tickers total, ~all SHORT. The miss is upstream of the council, not a threshold-tuning issue.
- **Benefit:** Biggest single lever on recall — likely a universe-seed or scan-emission/score-gate gap.
- **Risk:** Investigation only; no change until understood.
- **Complexity:** MEDIUM — needs the historizer + a few weeks of universe snapshots. · **Scope:** research-only · **Overfit risk:** none

### R3_deepen_price_cache — Deepen the price-history cache beyond ~110 bars
- **Evidence:** 119/246 winners are cache-limited; Voyager's 260-bar gates can't be reconstructed or back-tested faithfully.
- **Benefit:** Faithful PIT reconstruction and back-tests; better universe coverage.
- **Risk:** Provider-budget cost for backfill (one-time).
- **Complexity:** LOW-MEDIUM. · **Scope:** research-only (cache) · **Overfit risk:** none

### R4_momentum_RS_baseline_lane — Evaluate a regime-adaptive momentum/RS recall lane (research-only first)
- **Evidence:** A simple baseline ('rs_20d', recall 22.6%) caught far more forward winners than the funnel (1.8%). 54.6% of winners had a clean buyable window the system never took.
- **Benefit:** Materially higher recall on momentum leaders.
- **Risk:** Low precision (8-12%) — a recall lane needs a precision gate before any promotion; momentum drawdowns are real.
- **Complexity:** MEDIUM. · **Scope:** research-only · **Overfit risk:** MEDIUM — validate forward, do NOT tune to this winner set.

### R5_theme_leadership_radar — Build a research-only Theme Leadership Radar
- **Evidence:** Semiconductors: 59 winners, median max return ~105%, recall ~nil; coarse FMP labels hide memory/AI-hardware clusters.
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
> - Alpha board+overlay are historized via research_delta since ~2026-05-20 (~6 days); per-ticker Stock Lens + Gatekeeper were NOT — funnel_historizer.py now closes that gap for FUTURE autopsies.
> - Voyager 260-bar gates are indeterminate for ~110-bar cache names (119/246); not attributed to the scanner. The bars_needed filter audit is likewise marked INDETERMINATE (cache-confounded).
> - Forward precision is NOT_COMPUTABLE_YET (today's board has no forward window).
> - Root causes leaning on non-historized stages carry an _INFERRED suffix.
> - Theme classifier is profile-text-limited; memory/AI-hardware counts are lower bounds.

