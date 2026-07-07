# Scanner Truth Review — 2026-05 (Phase 1G.5)

*Generated 2026-07-07T00:33:27.551243+00:00 · 1G.5 — Scanner Truth Review (FULL: Tasks 1-11) · research-only, cache-only.*

## 1. Executive summary

- **Were market winners missed? YES.** Of **261** liquid winners ≥+80% (**161** ≥2x), only **6** ever touched any historized funnel stage → **winner recall 2.3%**.
- **They fell out before the council saw them.** The active long funnel (VOYAGER+SNIPER) logged only **4 distinct tickers**; veto_log is ~all SHORT. `UNIVERSE_MISS` dominates.
- **A dumb baseline beats the funnel on recall:** simple 20d-RS recall **34.0%** vs funnel **1.5%** on the same forward set.
- **A clean entry existed:** 9.2% of winners had a buyable window (median 26.0d) before becoming extended — the system simply never surfaced them.
- Consistent with design (Voyager buyable-pullback mandate; Alpha penalises large momentum leaders). The question is whether a **research-only momentum/RS recall lane** + **theme radar** is worth adding — validated forward, never curve-fit.

## 2. Were market winners missed?

- Liquid winners ≥+50%: **483**, ≥+80%: **261**, ≥2x: **161** (scanned 5549; 787 illiquid excluded).
- By theme: `other`=217, `unknown`=78, `biotech_healthcare`=77, `semiconductors`=57, `hardware`=27, `space_aerospace`=17, `memory_storage`=3, `quantum`=3, `nuclear_energy`=3, `crypto_blockchain`=1.

## 3. Top missed winners (liquid, by trailing max return)

| ticker | theme | max ret | $vol(M) | recall |
|---|---|--:|--:|:--:|
| INHD | other | 20071% | 551 | — |
| ASTC | space_aerospace | 1698% | 11 | — |
| STI | other | 842% | 104 | — |
| AGL | biotech_healthcare | 795% | 25 | — |
| RXT | space_aerospace | 675% | 133 | — |
| CAR | other | 660% | 152 | — |
| CODX | biotech_healthcare | 555% | 464 | — |
| MXL | semiconductors | 460% | 730 | — |
| MNTS | space_aerospace | 436% | 61 | — |
| WYY | other | 410% | 11 | — |
| SKLZ | other | 396% | 7 | — |
| AKTX | biotech_healthcare | 371% | 45 | — |
| LWLG | other | 354% | 88 | — |
| MRAM | semiconductors | 351% | 246 | — |
| BAND | other | 345% | 50 | — |
| TRT | semiconductors | 335% | 26 | — |
| OCC | other | 331% | 34 | — |
| APPS | other | 331% | 69 | — |
| FCEL | other | 312% | 298 | — |
| CRCA | unknown | 308% | 68 | — |
| SVCO | other | 306% | 8 | — |
| AXTI | semiconductors | 301% | 988 | — |
| AAOI | semiconductors | 296% | 2011 | — |
| NVTS | semiconductors | 285% | 1593 | — |
| ADV | other | 273% | 7 | — |

## 4. Where they fell out of the funnel

| root cause | count |
|---|--:|
| FILTER_TOO_STRICT | 145 |
| UNIVERSE_MISS | 83 |
| DATA_MISS | 28 |
| VALID_NO_TRADE | 5 |

Detection timing: early **1**, late **5**, blind **255**.

## 5. Recall / precision metrics

- Recall (ever in funnel): **2.3%**; ≥2x bucket **1.9%**.
- Semiconductors recall **4.5%** (n=44).
- Recall-before-move **NOT_RETAINED**; forward precision **NOT_COMPUTABLE_YET**.

## 6. Comparison vs simple baselines

As-of 2026-04-08, 60td forward, 265 forward winners in 2283 liquid names.

| baseline | flagged | recall | precision | avg fwd ret |
|---|--:|--:|--:|--:|
| rs_20d | 371 | 34.0% | 24.3% | 17% |
| high_50d_breakout | 199 | 11.3% | 15.1% | 11% |
| vol_strength | 97 | 7.9% | 21.6% | 9% |
| sector_rs | 296 | 30.2% | 27.0% | 19% |
| mom_20_60 | 201 | 16.2% | 21.4% | 13% |

**Verdict:** a SIMPLE baseline ('rs_20d', recall 34.0%) caught more forward winners than the live funnel (1.5%). Sophistication did not buy recall here.

## 7. Theme / sector leadership audit

| theme | winners | median max | on board | seen | visibility |
|---|--:|--:|--:|--:|---|
| other | 217 | 80% | 15 | 4 | visible_on_board |
| unknown | 78 | 68% | 0 | 0 | absent_from_board |
| biotech_healthcare | 77 | 84% | 3 | 0 | visible_on_board |
| semiconductors | 57 | 108% | 2 | 2 | visible_on_board |
| hardware | 27 | 89% | 0 | 0 | absent_from_board |
| space_aerospace | 17 | 96% | 0 | 0 | absent_from_board |
| memory_storage | 3 | 120% | 0 | 0 | absent_from_board |
| quantum | 3 | 107% | 0 | 0 | absent_from_board |
| nuclear_energy | 3 | 59% | 0 | 0 | absent_from_board |
| crypto_blockchain | 1 | 90% | 0 | 0 | absent_from_board |

*Limitation:* FMP industry taxonomy is coarse: memory & AI-hardware mostly read 'Semiconductors'/'Hardware, Equipment & Parts' and cannot be cleanly separated by profile. Theme counts are lower bounds for those clusters.

**Theme Leadership Radar:** PROPOSED — research-only, not built this phase — A nightly cache-only report that clusters the liquid universe by trailing relative-strength co-movement (correlation of 20/60d returns) to detect emerging strength CLUSTERS independent of the coarse FMP industry labels, and surfaces the top leaders per cluster.

## 8. Filter audit

| filter | threshold | winners rej | losers rej | recall cost | verdict |
|---|---|--:|--:|--:|---|
| liquidity_price | price∈[$5,$1000] | 449 | 1136 | 52.9% | BY-DESIGN exclusion |
| liquidity_dvol | avg$vol≥$5M & vol≥300k | 523 | 2470 | 61.7% | BY-DESIGN exclusion |
| voyager_max_extension_ma50 | >12% above MA50 → reject | 110 | 408 | 13.0% | KEEP |
| voyager_ma200_floor | price < MA200×0.92 → reject | 154 | 187 | 18.2% | KEEP |
| voyager_bars_needed_260 | <260 bars → reject | 515 | 3911 | 60.7% | INDETERMINATE |
| sniper_bars_needed_75 | <75 bars → reject | 472 | 3781 | 55.7% | INDETERMINATE |
| alpha_market_cap_band | mcap∉[$300M,$80B] | 73 | 189 | 8.6% | KEEP |

_Not reliably computable (disclosed, not guessed):_ voyager_rs_130 / fundamental_score; voyager_dvol_trend_ratio; sniper_vol_spike_1.4x / atr_contraction_0.85; earnings_safe_days; options_liquidity / 13F_sponsorship; top_25_board_cap.

## 9. Entry-state timing audit

- **9.2%** of winners had a clean buyable window before becoming extended (median **26.0 days**); funnel detected **1**.
- buyable = near MA50 (−8%..+12%), above MA200 floor where computable. A buyable window existing but no detection ⇒ the system had a clean early entry it did not take (ENTRY_VALIDATOR/UNIVERSE gap).

## 10. Recommendations (evidence-based)

### R1_historize_funnel — Historize the per-ticker funnel (lens + gatekeeper)
- **Evidence:** Recall-before-move and forward-precision are uncomputable; only ~6 days of board history exist.
- **Benefit:** Makes the NEXT autopsy faithful; enables forward precision.
- **Risk:** None (cache-only, additive).
- **Complexity:** LOW — funnel_historizer.py shipped; add a daily timer. · **Scope:** research-only · **Overfit risk:** none

### R2_investigate_emission_gap — Investigate why the LONG funnel surfaced only 4 tickers to the council
- **Evidence:** Winner recall 2.3%; veto_log has 27 distinct tickers total, ~all SHORT. The miss is upstream of the council, not a threshold-tuning issue.
- **Benefit:** Biggest single lever on recall — likely a universe-seed or scan-emission/score-gate gap.
- **Risk:** Investigation only; no change until understood.
- **Complexity:** MEDIUM — needs the historizer + a few weeks of universe snapshots. · **Scope:** research-only · **Overfit risk:** none

### R3_deepen_price_cache — Deepen the price-history cache beyond ~110 bars
- **Evidence:** 36/261 winners are cache-limited; Voyager's 260-bar gates can't be reconstructed or back-tested faithfully.
- **Benefit:** Faithful PIT reconstruction and back-tests; better universe coverage.
- **Risk:** Provider-budget cost for backfill (one-time).
- **Complexity:** LOW-MEDIUM. · **Scope:** research-only (cache) · **Overfit risk:** none

### R4_momentum_RS_baseline_lane — Evaluate a regime-adaptive momentum/RS recall lane (research-only first)
- **Evidence:** A simple baseline ('rs_20d', recall 34.0%) caught far more forward winners than the funnel (1.5%). 54.6% of winners had a clean buyable window the system never took.
- **Benefit:** Materially higher recall on momentum leaders.
- **Risk:** Low precision (8-12%) — a recall lane needs a precision gate before any promotion; momentum drawdowns are real.
- **Complexity:** MEDIUM. · **Scope:** research-only · **Overfit risk:** MEDIUM — validate forward, do NOT tune to this winner set.

### R5_theme_leadership_radar — Build a research-only Theme Leadership Radar
- **Evidence:** Semiconductors: 57 winners, median max return ~108%, recall ~nil; coarse FMP labels hide memory/AI-hardware clusters.
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
> - Voyager 260-bar gates are indeterminate for ~110-bar cache names (36/261); not attributed to the scanner. The bars_needed filter audit is likewise marked INDETERMINATE (cache-confounded).
> - Forward precision is NOT_COMPUTABLE_YET (today's board has no forward window).
> - Root causes leaning on non-historized stages carry an _INFERRED suffix.
> - Theme classifier is profile-text-limited; memory/AI-hardware counts are lower bounds.

