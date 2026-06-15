# Universe Forward Replay (A/B) Results — Phase 1G.8

*Generated 2026-06-09T01:08:46.846416+00:00 · research-only · cache-only. Does NOT modify the production universe.*

**Verdict:** `NEED_MORE_DATA` · **Promotion:** `NEED_MORE_DATA` (shadow dates: 3, ticker-days: {'production': 2000, 'proposed_dynamic': 2000}).

## Why NEED_MORE_DATA (today)
The shadow ledger has just begun; nothing has matured to a forward horizon yet, so the A/B metrics are empty and the promotion gates cannot pass. The framework accrues both universes nightly and will populate as bars mature.

## Promotion gates (Task 4)

| gate | threshold | status |
|---|---|---|
| ≥10 trading days | 10 | fail |
| ≥3000 ticker-days | 3000 | fail |
| early-winner recall ↑ ≥3pp | margin | fail |
| FP not worse (≤+2pp) | margin | fail |
| theme coverage ↑ | — | PASS |
| forward/top-decile ↑ | — | fail |
| no sector overconcentration (≤35%) | — | PASS |

**If any gate fails, production stays unchanged and shadow research continues.**

## Universe quality (latest snapshot)

| metric | production | proposed |
|---|--:|--:|
| size | 1000 | 1000 |
| early-stage % | 10.6 | 15.9 |
| late-stage % | 6.9 | 7.5 |
| leading-theme members | 61 | 68 |
| max sector share % | 28.3 | 22.0 |

### Theme coverage (semis / memory / space / nuclear / hardware)
- production: {'semiconductors': 61, 'memory_storage': 2, 'space_aerospace': 24, 'nuclear_energy': 16, 'hardware': 37}
- proposed: {'semiconductors': 57, 'memory_storage': 2, 'space_aerospace': 29, 'nuclear_energy': 8, 'hardware': 39}

## Score-gate interaction (Task 5)
- Proposed early leaders evaluated: **324**; pass either structural gate: **26**; killed by both: **298** (92.0%).
- Top voyager reject reasons: {'insufficient_history_260': 203, 'too_extended': 86, 'below_ma200_floor': 15, 'dvol_fading': 5}
- Top sniper reject reasons: {'volume_insufficient': 287, 'no_atr_contraction': 285, 'no_breakout': 274, 'ma50_not_rising': 87, 'insufficient_history_75': 6}

Many early leaders are rejected by the structural gates — note that insufficient_history_* rejections are CACHE-DEPTH artifacts (shallow cache), not real structure failures. Recommendation (DESIGN ONLY): route forward-validated RS/theme early leaders to the Stock Lens/Gatekeeper as a research-only second surface that BYPASSES the voyager/sniper score gates, rather than loosening those gates. No gate change is made here.

*Caveat: voyager_structural needs 260 bars; on the shallow cache most names fail on insufficient_history_260, so this audit is fidelity-limited until the deepening refresh completes.*

## Does proposed find candidates earlier / just add noise?
Undetermined until maturity. The dual-version ledger + this replay are the instrument that will answer it point-in-time. **No production change is made.**

