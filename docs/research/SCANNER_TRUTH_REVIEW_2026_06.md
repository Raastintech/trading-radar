# Scanner Truth Review — 2026-05 (Phase 1G.5)

*Generated 2026-06-27T15:58:46.828132+00:00 · 1G.5 — Scanner Truth Review (FULL: Tasks 1-11) · research-only, cache-only.*

## 1. Executive summary

- **Were market winners missed? YES.** Of **255** liquid winners ≥+80% (**169** ≥2x), only **5** ever touched any historized funnel stage → **winner recall 2.0%**.
- **They fell out before the council saw them.** The active long funnel (VOYAGER+SNIPER) logged only **4 distinct tickers**; veto_log is ~all SHORT. `UNIVERSE_MISS` dominates.
- **A dumb baseline beats the funnel on recall:** simple 20d-RS recall **19.6%** vs funnel **1.7%** on the same forward set.
- **A clean entry existed:** 30.6% of winners had a buyable window (median 28.0d) before becoming extended — the system simply never surfaced them.
- Consistent with design (Voyager buyable-pullback mandate; Alpha penalises large momentum leaders). The question is whether a **research-only momentum/RS recall lane** + **theme radar** is worth adding — validated forward, never curve-fit.

## 2. Were market winners missed?

- Liquid winners ≥+50%: **489**, ≥+80%: **255**, ≥2x: **169** (scanned 5548; 876 illiquid excluded).
- By theme: `other`=224, `unknown`=89, `semiconductors`=62, `biotech_healthcare`=59, `hardware`=30, `space_aerospace`=15, `quantum`=3, `memory_storage`=3, `nuclear_energy`=3, `crypto_blockchain`=1.

## 3. Top missed winners (liquid, by trailing max return)

| ticker | theme | max ret | $vol(M) | recall |
|---|---|--:|--:|:--:|
| INHD | other | 3085% | 551 | — |
| ASTC | space_aerospace | 1672% | 150 | — |
| RXT | unknown | 1640% | 47 | — |
| AGL | biotech_healthcare | 1015% | 25 | — |
| STI | other | 793% | 223 | — |
| SDOT | other | 781% | 37 | — |
| AXTI | semiconductors | 507% | 988 | — |
| CODX | biotech_healthcare | 495% | 464 | — |
| MXL | semiconductors | 488% | 730 | — |
| CAR | other | 485% | 152 | — |
| MNTS | space_aerospace | 430% | 73 | — |
| LWLG | other | 426% | 88 | — |
| AAOI | semiconductors | 408% | 2011 | — |
| MRAM | semiconductors | 401% | 246 | — |
| REPL | biotech_healthcare | 389% | 63 | — |
| SKLZ | other | 381% | 7 | — |
| BAND | other | 377% | 50 | — |
| OCC | other | 359% | 28 | — |
| WOLF | semiconductors | 350% | 845 | — |
| TRT | semiconductors | 308% | 26 | — |
| SHAZ | other | 308% | 85 | — |
| FCEL | other | 304% | 298 | — |
| CRCA | unknown | 302% | 68 | — |
| NVTS | semiconductors | 287% | 1593 | — |
| AEHR | semiconductors | 286% | 297 | — |

## 4. Where they fell out of the funnel

| root cause | count |
|---|--:|
| UNIVERSE_MISS | 115 |
| FILTER_TOO_STRICT | 106 |
| DATA_MISS | 31 |
| VALID_NO_TRADE | 3 |

Detection timing: early **0**, late **5**, blind **250**.

## 5. Recall / precision metrics

- Recall (ever in funnel): **2.0%**; ≥2x bucket **2.4%**.
- Semiconductors recall **4.7%** (n=43).
- Recall-before-move **NOT_RETAINED**; forward precision **NOT_COMPUTABLE_YET**.

## 6. Comparison vs simple baselines

As-of 2026-03-31, 60td forward, 286 forward winners in 2298 liquid names.

| baseline | flagged | recall | precision | avg fwd ret |
|---|--:|--:|--:|--:|
| rs_20d | 341 | 18.9% | 15.8% | 10% |
| high_50d_breakout | 89 | 3.5% | 11.2% | 11% |
| vol_strength | 110 | 4.9% | 12.7% | 7% |
| sector_rs | 327 | 19.6% | 17.1% | 12% |
| mom_20_60 | 164 | 8.4% | 14.6% | 9% |

**Verdict:** a SIMPLE baseline ('sector_rs', recall 19.6%) caught more forward winners than the live funnel (1.7%). Sophistication did not buy recall here.

## 7. Theme / sector leadership audit

| theme | winners | median max | on board | seen | visibility |
|---|--:|--:|--:|--:|---|
| other | 224 | 78% | 15 | 2 | visible_on_board |
| unknown | 89 | 69% | 0 | 0 | absent_from_board |
| semiconductors | 62 | 114% | 2 | 2 | visible_on_board |
| biotech_healthcare | 59 | 83% | 3 | 1 | visible_on_board |
| hardware | 30 | 107% | 0 | 0 | absent_from_board |
| space_aerospace | 15 | 118% | 0 | 0 | absent_from_board |
| quantum | 3 | 109% | 0 | 0 | absent_from_board |
| memory_storage | 3 | 165% | 0 | 0 | absent_from_board |
| nuclear_energy | 3 | 61% | 0 | 0 | absent_from_board |
| crypto_blockchain | 1 | 121% | 0 | 0 | absent_from_board |

*Limitation:* FMP industry taxonomy is coarse: memory & AI-hardware mostly read 'Semiconductors'/'Hardware, Equipment & Parts' and cannot be cleanly separated by profile. Theme counts are lower bounds for those clusters.

**Theme Leadership Radar:** PROPOSED — research-only, not built this phase — A nightly cache-only report that clusters the liquid universe by trailing relative-strength co-movement (correlation of 20/60d returns) to detect emerging strength CLUSTERS independent of the coarse FMP industry labels, and surfaces the top leaders per cluster.

## 8. Filter audit

| filter | threshold | winners rej | losers rej | recall cost | verdict |
|---|---|--:|--:|--:|---|
| liquidity_price | price∈[$5,$1000] | 512 | 1082 | 54.5% | BY-DESIGN exclusion |
| liquidity_dvol | avg$vol≥$5M & vol≥300k | 582 | 2371 | 61.9% | BY-DESIGN exclusion |
| voyager_max_extension_ma50 | >12% above MA50 → reject | 72 | 331 | 7.7% | KEEP |
| voyager_ma200_floor | price < MA200×0.92 → reject | 75 | 94 | 8.0% | KEEP |
| voyager_bars_needed_260 | <260 bars → reject | 772 | 4457 | 82.1% | INDETERMINATE |
| sniper_bars_needed_75 | <75 bars → reject | 724 | 4213 | 77.0% | INDETERMINATE |
| alpha_market_cap_band | mcap∉[$300M,$80B] | 72 | 178 | 7.7% | KEEP |

_Not reliably computable (disclosed, not guessed):_ voyager_rs_130 / fundamental_score; voyager_dvol_trend_ratio; sniper_vol_spike_1.4x / atr_contraction_0.85; earnings_safe_days; options_liquidity / 13F_sponsorship; top_25_board_cap.

## 9. Entry-state timing audit

- **30.6%** of winners had a clean buyable window before becoming extended (median **28.0 days**); funnel detected **0**.
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
- **Evidence:** 103/255 winners are cache-limited; Voyager's 260-bar gates can't be reconstructed or back-tested faithfully.
- **Benefit:** Faithful PIT reconstruction and back-tests; better universe coverage.
- **Risk:** Provider-budget cost for backfill (one-time).
- **Complexity:** LOW-MEDIUM. · **Scope:** research-only (cache) · **Overfit risk:** none

### R4_momentum_RS_baseline_lane — Evaluate a regime-adaptive momentum/RS recall lane (research-only first)
- **Evidence:** A simple baseline ('sector_rs', recall 19.6%) caught far more forward winners than the funnel (1.7%). 54.6% of winners had a clean buyable window the system never took.
- **Benefit:** Materially higher recall on momentum leaders.
- **Risk:** Low precision (8-12%) — a recall lane needs a precision gate before any promotion; momentum drawdowns are real.
- **Complexity:** MEDIUM. · **Scope:** research-only · **Overfit risk:** MEDIUM — validate forward, do NOT tune to this winner set.

### R5_theme_leadership_radar — Build a research-only Theme Leadership Radar
- **Evidence:** Semiconductors: 62 winners, median max return ~113%, recall ~nil; coarse FMP labels hide memory/AI-hardware clusters.
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
> - Voyager 260-bar gates are indeterminate for ~110-bar cache names (103/255); not attributed to the scanner. The bars_needed filter audit is likewise marked INDETERMINATE (cache-confounded).
> - Forward precision is NOT_COMPUTABLE_YET (today's board has no forward window).
> - Root causes leaning on non-historized stages carry an _INFERRED suffix.
> - Theme classifier is profile-text-limited; memory/AI-hardware counts are lower bounds.

