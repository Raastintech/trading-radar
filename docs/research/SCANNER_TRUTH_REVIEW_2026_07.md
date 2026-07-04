# Scanner Truth Review — 2026-05 (Phase 1G.5)

*Generated 2026-07-04T00:38:05.552792+00:00 · 1G.5 — Scanner Truth Review (FULL: Tasks 1-11) · research-only, cache-only.*

## 1. Executive summary

- **Were market winners missed? YES.** Of **281** liquid winners ≥+80% (**186** ≥2x), only **5** ever touched any historized funnel stage → **winner recall 1.8%**.
- **They fell out before the council saw them.** The active long funnel (VOYAGER+SNIPER) logged only **4 distinct tickers**; veto_log is ~all SHORT. `UNIVERSE_MISS` dominates.
- **A dumb baseline beats the funnel on recall:** simple 20d-RS recall **25.4%** vs funnel **1.4%** on the same forward set.
- **A clean entry existed:** 8.5% of winners had a buyable window (median 26.0d) before becoming extended — the system simply never surfaced them.
- Consistent with design (Voyager buyable-pullback mandate; Alpha penalises large momentum leaders). The question is whether a **research-only momentum/RS recall lane** + **theme radar** is worth adding — validated forward, never curve-fit.

## 2. Were market winners missed?

- Liquid winners ≥+50%: **527**, ≥+80%: **281**, ≥2x: **186** (scanned 5549; 823 illiquid excluded).
- By theme: `other`=238, `unknown`=90, `biotech_healthcare`=78, `semiconductors`=62, `hardware`=30, `space_aerospace`=17, `nuclear_energy`=5, `memory_storage`=3, `quantum`=3, `crypto_blockchain`=1.

## 3. Top missed winners (liquid, by trailing max return)

| ticker | theme | max ret | $vol(M) | recall |
|---|---|--:|--:|:--:|
| INHD | other | 13257% | 551 | — |
| ASTC | space_aerospace | 1666% | 16 | — |
| STI | other | 850% | 223 | — |
| RXT | space_aerospace | 736% | 131 | — |
| CAR | other | 714% | 152 | — |
| AGL | biotech_healthcare | 714% | 25 | — |
| CODX | biotech_healthcare | 542% | 464 | — |
| MNTS | space_aerospace | 504% | 67 | — |
| MXL | semiconductors | 470% | 730 | — |
| LWLG | other | 416% | 88 | — |
| SKLZ | other | 398% | 7 | — |
| AXTI | semiconductors | 395% | 988 | — |
| OCC | other | 386% | 33 | — |
| AKTX | biotech_healthcare | 381% | 45 | — |
| MRAM | semiconductors | 373% | 246 | — |
| WYY | other | 367% | 10 | — |
| APPS | other | 341% | 68 | — |
| BAND | other | 340% | 50 | — |
| TRT | semiconductors | 338% | 26 | — |
| FCEL | other | 321% | 298 | — |
| SDOT | other | 316% | 19 | — |
| SVCO | other | 314% | 8 | — |
| AAOI | semiconductors | 313% | 2011 | — |
| CRCA | unknown | 312% | 68 | — |
| REPL | biotech_healthcare | 305% | 52 | — |

## 4. Where they fell out of the funnel

| root cause | count |
|---|--:|
| FILTER_TOO_STRICT | 142 |
| UNIVERSE_MISS | 105 |
| DATA_MISS | 30 |
| VALID_NO_TRADE | 4 |

Detection timing: early **1**, late **4**, blind **276**.

## 5. Recall / precision metrics

- Recall (ever in funnel): **1.8%**; ≥2x bucket **1.6%**.
- Semiconductors recall **4.3%** (n=47).
- Recall-before-move **NOT_RETAINED**; forward precision **NOT_COMPUTABLE_YET**.

## 6. Comparison vs simple baselines

As-of 2026-04-07, 60td forward, 287 forward winners in 2275 liquid names.

| baseline | flagged | recall | precision | avg fwd ret |
|---|--:|--:|--:|--:|
| rs_20d | 365 | 25.4% | 20.0% | 13% |
| high_50d_breakout | 116 | 4.5% | 11.2% | 5% |
| vol_strength | 93 | 5.9% | 18.3% | 3% |
| sector_rs | 303 | 24.7% | 23.4% | 16% |
| mom_20_60 | 175 | 10.5% | 17.1% | 8% |

**Verdict:** a SIMPLE baseline ('rs_20d', recall 25.4%) caught more forward winners than the live funnel (1.4%). Sophistication did not buy recall here.

## 7. Theme / sector leadership audit

| theme | winners | median max | on board | seen | visibility |
|---|--:|--:|--:|--:|---|
| other | 238 | 80% | 15 | 3 | visible_on_board |
| unknown | 90 | 66% | 0 | 0 | absent_from_board |
| biotech_healthcare | 78 | 84% | 3 | 0 | visible_on_board |
| semiconductors | 62 | 110% | 2 | 2 | visible_on_board |
| hardware | 30 | 101% | 0 | 0 | absent_from_board |
| space_aerospace | 17 | 103% | 0 | 0 | absent_from_board |
| nuclear_energy | 5 | 61% | 0 | 0 | absent_from_board |
| memory_storage | 3 | 137% | 0 | 0 | absent_from_board |
| quantum | 3 | 119% | 0 | 0 | absent_from_board |
| crypto_blockchain | 1 | 108% | 0 | 0 | absent_from_board |

*Limitation:* FMP industry taxonomy is coarse: memory & AI-hardware mostly read 'Semiconductors'/'Hardware, Equipment & Parts' and cannot be cleanly separated by profile. Theme counts are lower bounds for those clusters.

**Theme Leadership Radar:** PROPOSED — research-only, not built this phase — A nightly cache-only report that clusters the liquid universe by trailing relative-strength co-movement (correlation of 20/60d returns) to detect emerging strength CLUSTERS independent of the coarse FMP industry labels, and surfaces the top leaders per cluster.

## 8. Filter audit

| filter | threshold | winners rej | losers rej | recall cost | verdict |
|---|---|--:|--:|--:|---|
| liquidity_price | price∈[$5,$1000] | 475 | 1127 | 52.3% | BY-DESIGN exclusion |
| liquidity_dvol | avg$vol≥$5M & vol≥300k | 556 | 2438 | 61.2% | BY-DESIGN exclusion |
| voyager_max_extension_ma50 | >12% above MA50 → reject | 78 | 378 | 8.6% | KEEP |
| voyager_ma200_floor | price < MA200×0.92 → reject | 160 | 223 | 17.6% | KEEP |
| voyager_bars_needed_260 | <260 bars → reject | 542 | 3880 | 59.7% | INDETERMINATE |
| sniper_bars_needed_75 | <75 bars → reject | 503 | 3755 | 55.4% | INDETERMINATE |
| alpha_market_cap_band | mcap∉[$300M,$80B] | 70 | 189 | 7.7% | KEEP |

_Not reliably computable (disclosed, not guessed):_ voyager_rs_130 / fundamental_score; voyager_dvol_trend_ratio; sniper_vol_spike_1.4x / atr_contraction_0.85; earnings_safe_days; options_liquidity / 13F_sponsorship; top_25_board_cap.

## 9. Entry-state timing audit

- **8.5%** of winners had a clean buyable window before becoming extended (median **26.0 days**); funnel detected **1**.
- buyable = near MA50 (−8%..+12%), above MA200 floor where computable. A buyable window existing but no detection ⇒ the system had a clean early entry it did not take (ENTRY_VALIDATOR/UNIVERSE gap).

## 10. Recommendations (evidence-based)

### R1_historize_funnel — Historize the per-ticker funnel (lens + gatekeeper)
- **Evidence:** Recall-before-move and forward-precision are uncomputable; only ~6 days of board history exist.
- **Benefit:** Makes the NEXT autopsy faithful; enables forward precision.
- **Risk:** None (cache-only, additive).
- **Complexity:** LOW — funnel_historizer.py shipped; add a daily timer. · **Scope:** research-only · **Overfit risk:** none

### R2_investigate_emission_gap — Investigate why the LONG funnel surfaced only 4 tickers to the council
- **Evidence:** Winner recall 1.8%; veto_log has 27 distinct tickers total, ~all SHORT. The miss is upstream of the council, not a threshold-tuning issue.
- **Benefit:** Biggest single lever on recall — likely a universe-seed or scan-emission/score-gate gap.
- **Risk:** Investigation only; no change until understood.
- **Complexity:** MEDIUM — needs the historizer + a few weeks of universe snapshots. · **Scope:** research-only · **Overfit risk:** none

### R3_deepen_price_cache — Deepen the price-history cache beyond ~110 bars
- **Evidence:** 35/281 winners are cache-limited; Voyager's 260-bar gates can't be reconstructed or back-tested faithfully.
- **Benefit:** Faithful PIT reconstruction and back-tests; better universe coverage.
- **Risk:** Provider-budget cost for backfill (one-time).
- **Complexity:** LOW-MEDIUM. · **Scope:** research-only (cache) · **Overfit risk:** none

### R4_momentum_RS_baseline_lane — Evaluate a regime-adaptive momentum/RS recall lane (research-only first)
- **Evidence:** A simple baseline ('rs_20d', recall 25.4%) caught far more forward winners than the funnel (1.4%). 54.6% of winners had a clean buyable window the system never took.
- **Benefit:** Materially higher recall on momentum leaders.
- **Risk:** Low precision (8-12%) — a recall lane needs a precision gate before any promotion; momentum drawdowns are real.
- **Complexity:** MEDIUM. · **Scope:** research-only · **Overfit risk:** MEDIUM — validate forward, do NOT tune to this winner set.

### R5_theme_leadership_radar — Build a research-only Theme Leadership Radar
- **Evidence:** Semiconductors: 62 winners, median max return ~109%, recall ~nil; coarse FMP labels hide memory/AI-hardware clusters.
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
> - Voyager 260-bar gates are indeterminate for ~110-bar cache names (35/281); not attributed to the scanner. The bars_needed filter audit is likewise marked INDETERMINATE (cache-confounded).
> - Forward precision is NOT_COMPUTABLE_YET (today's board has no forward window).
> - Root causes leaning on non-historized stages carry an _INFERRED suffix.
> - Theme classifier is profile-text-limited; memory/AI-hardware counts are lower bounds.

