# Scanner Truth Review — 2026-05 (Phase 1G.5)

*Generated 2026-07-22T00:33:21.939202+00:00 · 1G.5 — Scanner Truth Review (FULL: Tasks 1-11) · research-only, cache-only.*

## 1. Executive summary

- **Were market winners missed? YES.** Of **257** liquid winners ≥+80% (**172** ≥2x), only **0** ever touched any historized funnel stage → **winner recall 0.0%**.
- **They fell out before the council saw them.** The active long funnel (VOYAGER+SNIPER) logged only **4 distinct tickers**; veto_log is ~all SHORT. `UNIVERSE_MISS` dominates.
- **A dumb baseline beats the funnel on recall:** simple 20d-RS recall **35.9%** vs funnel **0.0%** on the same forward set.
- **A clean entry existed:** 4.7% of winners had a buyable window (median 30.5d) before becoming extended — the system simply never surfaced them.
- Consistent with design (Voyager buyable-pullback mandate; Alpha penalises large momentum leaders). The question is whether a **research-only momentum/RS recall lane** + **theme radar** is worth adding — validated forward, never curve-fit.

## 2. Were market winners missed?

- Liquid winners ≥+50%: **481**, ≥+80%: **257**, ≥2x: **172** (scanned 5655; 618 illiquid excluded).
- By theme: `other`=221, `biotech_healthcare`=86, `semiconductors`=64, `unknown`=59, `hardware`=31, `space_aerospace`=12, `memory_storage`=3, `quantum`=3, `nuclear_energy`=1, `crypto_blockchain`=1.

## 3. Top missed winners (liquid, by trailing max return)

| ticker | theme | max ret | $vol(M) | recall |
|---|---|--:|--:|:--:|
| AGL | biotech_healthcare | 715% | 41 | — |
| MXL | semiconductors | 658% | 358 | — |
| CAR | other | 606% | 152 | — |
| AMPG | other | 413% | 21 | — |
| FCEL | other | 406% | 370 | — |
| BAND | other | 400% | 81 | — |
| MRAM | semiconductors | 381% | 28 | — |
| ABSI | biotech_healthcare | 374% | 76 | — |
| BLZE | other | 373% | 77 | — |
| SHAZ | other | 369% | 145 | — |
| EVC | other | 360% | 23 | — |
| WYY | other | 359% | 12 | — |
| PENG | hardware | 340% | 373 | — |
| SVCO | other | 314% | 6 | — |
| REPL | biotech_healthcare | 308% | 103 | — |
| WOLF | semiconductors | 304% | 845 | — |
| QTTB | biotech_healthcare | 289% | 48 | — |
| ALAB | semiconductors | 287% | 1927 | — |
| APPS | other | 286% | 47 | — |
| VSH | semiconductors | 271% | 356 | — |
| BRUN | other | 268% | 43 | — |
| BB | other | 266% | 350 | — |
| ARM | semiconductors | 266% | 2147 | — |
| LWLG | other | 263% | 88 | — |
| SNDK | memory_storage | 256% | 24027 | — |

## 4. Where they fell out of the funnel

| root cause | count |
|---|--:|
| FILTER_TOO_STRICT | 131 |
| UNIVERSE_MISS | 108 |
| DATA_MISS | 18 |

Detection timing: early **0**, late **0**, blind **257**.

## 5. Recall / precision metrics

- Recall (ever in funnel): **0.0%**; ≥2x bucket **0.0%**.
- Semiconductors recall **0.0%** (n=50).
- Recall-before-move **NOT_RETAINED**; forward precision **NOT_COMPUTABLE_YET**.

## 6. Comparison vs simple baselines

As-of 2026-04-23, 60td forward, 206 forward winners in 2308 liquid names.

| baseline | flagged | recall | precision | avg fwd ret |
|---|--:|--:|--:|--:|
| rs_20d | 341 | 35.9% | 21.7% | 5% |
| high_50d_breakout | 220 | 13.1% | 12.3% | 2% |
| vol_strength | 96 | 9.2% | 19.8% | 3% |
| sector_rs | 368 | 32.5% | 18.2% | 4% |
| mom_20_60 | 263 | 25.7% | 20.2% | 6% |

**Verdict:** a SIMPLE baseline ('rs_20d', recall 35.9%) caught more forward winners than the live funnel (0.0%). Sophistication did not buy recall here.

## 7. Theme / sector leadership audit

| theme | winners | median max | on board | seen | visibility |
|---|--:|--:|--:|--:|---|
| other | 221 | 77% | 0 | 0 | absent_from_board |
| biotech_healthcare | 86 | 89% | 0 | 0 | absent_from_board |
| semiconductors | 64 | 107% | 0 | 0 | absent_from_board |
| unknown | 59 | 67% | 0 | 0 | absent_from_board |
| hardware | 31 | 88% | 0 | 0 | absent_from_board |
| space_aerospace | 12 | 104% | 0 | 0 | absent_from_board |
| memory_storage | 3 | 178% | 0 | 0 | absent_from_board |
| quantum | 3 | 63% | 0 | 0 | absent_from_board |
| nuclear_energy | 1 | 106% | 0 | 0 | absent_from_board |
| crypto_blockchain | 1 | 92% | 0 | 0 | absent_from_board |

*Limitation:* FMP industry taxonomy is coarse: memory & AI-hardware mostly read 'Semiconductors'/'Hardware, Equipment & Parts' and cannot be cleanly separated by profile. Theme counts are lower bounds for those clusters.

**Theme Leadership Radar:** PROPOSED — research-only, not built this phase — A nightly cache-only report that clusters the liquid universe by trailing relative-strength co-movement (correlation of 20/60d returns) to detect emerging strength CLUSTERS independent of the coarse FMP industry labels, and surfaces the top leaders per cluster.

## 8. Filter audit

| filter | threshold | winners rej | losers rej | recall cost | verdict |
|---|---|--:|--:|--:|---|
| liquidity_price | price∈[$5,$1000] | 290 | 1270 | 49.0% | BY-DESIGN exclusion |
| liquidity_dvol | avg$vol≥$5M & vol≥300k | 353 | 2754 | 59.6% | BY-DESIGN exclusion |
| voyager_max_extension_ma50 | >12% above MA50 → reject | 176 | 683 | 29.7% | REVIEW |
| voyager_ma200_floor | price < MA200×0.92 → reject | 102 | 200 | 17.2% | KEEP |
| voyager_bars_needed_260 | <260 bars → reject | 312 | 4021 | 52.7% | INDETERMINATE |
| sniper_bars_needed_75 | <75 bars → reject | 28 | 193 | 4.7% | INDETERMINATE |
| alpha_market_cap_band | mcap∉[$300M,$80B] | 66 | 229 | 11.1% | KEEP |

_Not reliably computable (disclosed, not guessed):_ voyager_rs_130 / fundamental_score; voyager_dvol_trend_ratio; sniper_vol_spike_1.4x / atr_contraction_0.85; earnings_safe_days; options_liquidity / 13F_sponsorship; top_25_board_cap.

## 9. Entry-state timing audit

- **4.7%** of winners had a clean buyable window before becoming extended (median **30.5 days**); funnel detected **0**.
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
- **Evidence:** 22/257 winners are cache-limited; Voyager's 260-bar gates can't be reconstructed or back-tested faithfully.
- **Benefit:** Faithful PIT reconstruction and back-tests; better universe coverage.
- **Risk:** Provider-budget cost for backfill (one-time).
- **Complexity:** LOW-MEDIUM. · **Scope:** research-only (cache) · **Overfit risk:** none

### R4_momentum_RS_baseline_lane — Evaluate a regime-adaptive momentum/RS recall lane (research-only first)
- **Evidence:** A simple baseline ('rs_20d', recall 35.9%) caught far more forward winners than the funnel (0.0%). 54.6% of winners had a clean buyable window the system never took.
- **Benefit:** Materially higher recall on momentum leaders.
- **Risk:** Low precision (8-12%) — a recall lane needs a precision gate before any promotion; momentum drawdowns are real.
- **Complexity:** MEDIUM. · **Scope:** research-only · **Overfit risk:** MEDIUM — validate forward, do NOT tune to this winner set.

### R5_theme_leadership_radar — Build a research-only Theme Leadership Radar
- **Evidence:** Semiconductors: 64 winners, median max return ~107%, recall ~nil; coarse FMP labels hide memory/AI-hardware clusters.
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
> - Voyager 260-bar gates are indeterminate for ~110-bar cache names (22/257); not attributed to the scanner. The bars_needed filter audit is likewise marked INDETERMINATE (cache-confounded).
> - Forward precision is NOT_COMPUTABLE_YET (today's board has no forward window).
> - Root causes leaning on non-historized stages carry an _INFERRED suffix.
> - Theme classifier is profile-text-limited; memory/AI-hardware counts are lower bounds.

