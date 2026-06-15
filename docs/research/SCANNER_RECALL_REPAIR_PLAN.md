# Scanner Recall Repair Plan — Phase 1G.6

*Research-only. Cache-only evidence. No execution, governance, paper-signal,
strategy-registry, or live-capital change is made or proposed for immediate
deployment. Every recommendation below is a research conclusion with an explicit
gate, not an instruction to flip a switch.*

Generated from the Phase 1G.6 package:
`research/scanner_emission_gap_audit.py`, `research/rs_recall_lane.py`,
`research/theme_leadership_radar.py`, `research/scanner_cap_audit.py`,
`research/price_cache_coverage_audit.py`, aggregated by
`research/scanner_recall_repair.py`
(run: `./scripts/run_research_cycle.sh scanner-recall`).

---

## 1. Executive summary

The production discovery funnel caught **2.1%** of the recent +50%/60d winner
universe. A trivially simple 20-day relative-strength rule caught **26.5%** of the
same winners over the same window. **The sophistication of the scanner did not buy
recall — it bought a structural narrowing that loses winners before any council
or governance layer ever sees them.**

The winner-retention funnel (emission-gap audit):

| stage | universe | winners retained | recall | dropped here |
|---|--:|--:|--:|--:|
| raw price universe | 5559 | 507 | 100% | 0 |
| liquidity-eligible | 2290 | 461 | 90.9% | 46 |
| base universe top-1000 | 1000 | 341 | 67.3% | 124 |
| **long strategy universe** | **135** | **40** | **7.9%** | **301** |
| alpha candidate band | ~320 | (not historized) | — | — |
| alpha board (≤20) | 20 | 10 | 2.0% | 35 |
| council (veto_log) | 27 | 1 | 0.2% | — |
| paper signals | 31 | 1 | 0.2% | — |
| decisions | 16 | 1 | 0.2% | — |

The collapse is upstream of the council. This is an **emission/universe gap**, not
a governance rejection. The council only ever saw **1** of 507 winners because no
scanner emitted the rest.

The two honest counterweights, stated up front so this plan is not read as a
green light:
1. The simple RS lane's recall advantage comes from casting a **wide net**
   (flagged ~956 of 2287 liquid names). At that width its precision is **7.7%**
   and a **random** pick of the same size scored **40.1%** recall vs the lane's
   **36.6%** — i.e. at current width *the lane has no selection edge over random*.
   RS catches *continuation* of existing strength; many +50% winners are
   news/reversal names that no momentum rule would have flagged early.
2. The whole review runs on a price cache that is **uniformly ~111 bars deep**
   (only 123/5559 tickers have ≥260 bars; MA200 computable for ~2%). The
   Voyager-structural and MA200 verdicts in the scanner-truth review are therefore
   **fidelity-limited** and must be re-confirmed on a deeper cache before being
   trusted.

**Bottom line:** the funnel is genuinely too narrow and a high-recall discovery
*surface* is justified — but it must feed the Lens/Gatekeeper for precision, get
its own small ranked cap, and never become a wide-net auto-trader.

---

## 2. Confirmed emission-gap root cause

- **Primary:** per-strategy score gates + structural filters (`4_long_strategy_universe`)
  cut 341 → 40 winners. Voyager (54 names) and Sniper (90 names) qualify a tiny,
  trend-following slice; recent-IPO / young / reversal winners are structurally
  excluded (Voyager needs 260 bars; both demand established trend/breakout
  structure).
- **Secondary:** the top-1000 base-liquidity seed drops 124 winners (26.9% of
  liquidity-eligible) purely on liquidity *rank*, and the ≤20 Alpha board retains
  only 10 — the binding cap on everything downstream.
- **Not the cause:** council/governance. It rejected ~nothing; it simply was never
  shown the winners (`NO_EMISSION_PATH`).
- **Live confirmation:** the theme radar shows **semiconductors and hardware are
  LEADING right now** (median 20–22% 20-day return, +15–17% RS vs SPY, 27–37
  breadth leaders) with **zero** of their leaders on the Alpha board — the emission
  gap reproduced on today's tape, not just in backtest.

---

## 3. Is Alpha Discovery over-filtered?

**Yes, for recall; appropriately tight for precision.** The board is a ≤20-name,
precision-oriented surface and should stay that way. The error is using a single
precision-tuned surface as the *only* discovery path. There is no parallel
high-recall surface, so anything the board's scoring de-prioritizes is invisible
system-wide. Lens artifacts exist for ~78 winners yet only ~10 reach the board:
that is a **routing/cap** problem, not a lens-coverage problem.

---

## 4. Should a simple RS lane be a permanent discovery lane?

**Yes — as a feeder, not a trader, and only after a forward-validation gate.**
The lane materially restores recall (26.5–36.6% vs 2.1%) and on today's tape
surfaces the leading semiconductor/hardware cluster the board misses. But:
- It has **no precision edge over random at wide width** — so it must run with a
  **small ranked cap** (e.g. top 15–25 by RS) and hand those names to the Lens /
  Gatekeeper, which supply the precision.
- It must use a **short history floor (≤60 bars)** so young movers are not excluded
  the way Voyager's 260-bar gate excludes them.
- It must be validated **forward** (the forward-resolution historizer enables this)
  before any of its output influences sizing or selection. Do **not** promote it on
  the strength of a backtest that was used to design it.

---

## 5. Should the Theme Leadership Radar feed Alpha scoring later?

**Eventually, as an additive feature behind a gate — not now.** The radar is
currently *descriptive*: it clusters the liquid universe by (coarse) theme and
labels each LEADING/EMERGING/EXTENDED/FADING/NOT_CONFIRMED. Its immediate value is
operator visibility (it caught the semis/hardware leadership the board missed). A
cluster-strength score *could* later become an additive Alpha feature, but only
after: (a) the FMP theme taxonomy limitation is addressed (memory/AI-hardware fold
into semis/hardware today), and (b) a forward gate shows cluster strength predicts
forward winners out-of-sample. Until then it is a radar, not a scorer.

---

## 6. Should top-N caps change?

**Not by raising numbers in isolation.** Caps amplify an already-narrow funnel;
widening the seed or board without a higher-recall feeder mostly adds low-quality
names (winner base rate ~9%). The right move is a **separate ranked RS-lane cap**
feeding the Lens, leaving the board's precision intact. A *regime-dynamic* seed
size (wider in high-dispersion regimes) is plausible but is a later, separately
validated step.

---

## 7. Must the price cache deepen?

**Yes — this is a prerequisite for trusting any 200-day/260-bar conclusion.** The
research cache holds a median of **111 bars** for ~94% of tickers; MA200 is
computable for ~2% and the Voyager 260-bar floor for ~2%. On this cache those gates
are effectively non-functional and the scanner-truth review's Voyager-structural
pass/fail cannot be honestly recomputed. **Caveat:** production Voyager may fetch
deeper history live from Alpaca; this audit only measures `cache/prices/*.parquet`.
Deepening the cache to ≥300 bars (via a *separate, explicitly approved* refresh, not
this package) is needed before any long-horizon conclusion is acted on.

---

## 8. What should NOT change yet

- No new strategy, no registry change, no paper signals, no trade proposals.
- No execution / governance / live-capital change. Holdout stays gated to 2026-12-01.
- No cap raised, no threshold loosened in production.
- No timer wiring for this package (operator-invoked `scanner-recall` only).
- No mutation/backfill of historical evidence; all reports are read-only.
- The Alpha board stays precision-tight; do not widen it to chase recall.

---

## 9. Overfitting warnings

- The RS lane's labels/thresholds were chosen from the *same* winner set used to
  measure them. The reported recall is **in-sample** and must not be treated as an
  expected forward number. The random-control comparison (lane 36.6% vs random
  40.1%) is the honest check, and it says the lane has no width-adjusted edge.
- "Recall vs past winners" is a survivorship-shaped target. Tuning any gate to
  recover named past winners (BNAI +1645%, etc.) is explicitly forbidden and would
  manufacture false confidence.
- Theme clustering is descriptive; using it as a score requires a forward gate.
- Cache-depth fidelity limits all MA200/260-bar conclusions — treat them as
  provisional until the cache is deepened.

---

## 10. Recommended next implementation phase

**Phase 1G.7 (proposed, research-only first):** stand up the RS recall lane as a
*forward-validated feeder*:
1. Run the lane nightly (cache-only) with a short history floor and a **ranked
   top-N cap**; historize its picks for forward scoring (no signals).
2. After ≥1 forward maturity window, measure forward precision/recall of the
   *ranked-capped* lane vs the board and vs random — out-of-sample.
3. Only if it passes, route its top names into Lens/Gatekeeper prebuild as a
   second discovery surface (still no execution).
4. Separately and explicitly, deepen the price cache to ≥300 bars and re-run the
   scanner-truth review to confirm/withdraw the Voyager-structural conclusions.

### Recommendation ledger

| # | Recommendation | Evidence | Expected recall ↑ | Precision risk | Overfit risk | Provider/API cost | Complexity | Scope |
|---|---|---|---|---|---|---|---|---|
| R1 | Add RS recall lane as a **ranked-capped feeder** (top 15–25) into Lens/Gatekeeper | lane recall 26.5–36.6% vs system 2.1%; semis/hardware leadership invisible to board | HIGH (3–10× on recall) | MEDIUM — wide width = no edge; ranked cap mitigates | LOW if forward-gated; HIGH if promoted on backtest | NONE for scan; LOW for lens prebuild on small set | MEDIUM | research-only → production feeder after gate |
| R2 | Use a **short history floor (≤60 bars)** in the lane | 468/507 winners < 260 bars; cache median 111 | MEDIUM | LOW | LOW | NONE | LOW | research-only |
| R3 | Keep Alpha board **precision-tight**; do not widen to chase recall | board precision is its purpose; widening adds 91% non-winners | n/a (protects precision) | n/a | n/a | unchanged | n/a | no change |
| R4 | Theme radar as **operator visibility now**, additive Alpha feature **later behind a gate** | semis/hardware LEADING, 0 board coverage | LOW now / MEDIUM later | LOW (descriptive) | MEDIUM if scored | NONE | LOW now | research-only |
| R5 | **Deepen price cache to ≥300 bars** (separate approved refresh) then re-run scanner-truth | MA200 computable for 2%; 260-bar gate non-functional | n/a (fidelity) | n/a | n/a | PROVIDER (one-off backfill) | MEDIUM | separate approved op |
| R6 | Do **not** raise base-1000 / board caps in isolation | caps amplify a narrow funnel; base rate ~9% | LOW | HIGH if done alone | LOW | enrichment cost scales | LOW | no change (deferred) |
| R7 | Consider **regime-dynamic seed size** only after forward validation | 124 winners below liquidity-rank cap | MEDIUM | MEDIUM | MEDIUM | LOW | MEDIUM | research-only (deferred) |

---

---

## Surfacing (Task 7) — sidecar hook, not wired yet

Per the repo's "do not overbuild dashboard UI; wire only after the report
stabilizes" doctrine, this package does **not** modify the dashboard or the MCP
audit orchestrator. Instead the aggregate sidecar
`cache/research/scanner_recall_repair_latest.json` carries two pre-rendered,
cache-only hooks under `summary`:

- `summary.mcp_summary_block` — `{system_recall, rs_recall, emission_gap_stage,
  leading_theme, action_needed}` for a future `SCANNER RECALL` MCP-audit block.
- `summary.dashboard_line` — e.g.
  `Scanner Recall: system 2.1% · RS 36.6% · gap=long_strategy_universe · leading=semiconductors,hardware`
  for a single dashboard line.

Wiring either is a trivial cache read with zero computation and zero provider
calls — deferred to a follow-up once the package has run a few cycles and the
numbers stabilize. The dashboard remains cache-only regardless.

---

---

## Phase 1G.7 — Emission-gap repair DESIGN (design only, not implemented)

*This section is design. No score gate, cap, or scoring weight is changed by
Phase 1G.7. Implementation is gated on the RS-lane forward validation
(`research/rs_recall_forward_validation.py`) reaching `READY_TO_FEED_LENS` and the
price cache being deepened (Task 1). The known break:*

```
461 liquidity-eligible winners
 → 341  after top-1000 base cap
 →  40  after per-strategy SCORE GATES + structural filters   ← biggest loss
 →  10  board cap (≤20, 10/track)
 →   1  council
```

### D1. Are the per-strategy score gates too strict?
**Likely yes for *recall*, but the evidence is currently fidelity-limited.** The
gates cut 341→40 (88% of surviving winners). But two of the structural gates —
Voyager's 260-bar floor and the MA200 floor — are **non-functional on the shallow
research cache** (MA200 computable for ~2%; see `price_cache_coverage_audit.py`
Task 2 buckets). So we cannot yet honestly attribute the 341→40 loss between
"genuinely strict scoring" and "gates failing for lack of history." **Prerequisite:
deepen the cache (Task 1), re-run the scanner-truth Voyager-structural audit, then
re-measure this drop.** Do not loosen gates before that.

### D2. Should score gates become regime/theme adaptive?
**Plausible, but only after forward validation — not now.** Voyager/Sniper encode
a trend-following, established-structure prior that structurally excludes young/
reversal winners regardless of regime. A regime- or dispersion-aware gate (looser
in high-dispersion regimes) could recover recall, but it is a production scoring
change and must clear its own forward gate. Theme-adaptive gating is downstream of
a *validated* theme signal (Theme Radar is still descriptive — see D5).

### D3. Should the RS/theme lane bypass the old score gates but still go to Lens/Gatekeeper?
**Yes — this is the recommended primary repair, and it is the safest.** Rather than
loosening the production gates (which risks the existing sleeves' precision), route
the RS recall lane's **ranked top-15–25** picks into the Lens/Gatekeeper as a
*parallel* discovery surface that does **not** pass through Voyager/Sniper scoring.
Precision is then supplied by the Lens/Gatekeeper, not by the lane. This is
strictly additive: the existing funnel is unchanged; a second surface is added.
**Gate:** only after `rs_recall_forward_validation` → `READY_TO_FEED_LENS`.

### D4. Should the board cap reserve slots for RS/theme leaders?
**Design: yes, a small reserved sub-quota — but implement as a SEPARATE lane cap,
not by shrinking the board.** Option A (preferred): give the RS/theme lane its own
small enrichment/Lens cap (see D6) and leave the 20-name board untouched. Option B:
reserve e.g. 3–5 board slots for forward-validated RS/theme leaders not already
present. Option A keeps the board's precision contract intact and is preferred.

### D5. Should theme leadership add a ranking boost?
**Not yet — descriptive only until forward-validated.** The Theme Radar now
historizes snapshots and runs a forward-continuation check
(`theme_leadership_radar.py --historize`). Only after that check shows leading
themes' top members forward-outperform SPY over the continuation horizon should a
**small additive** theme-strength term be considered as an Alpha feature — behind a
forward gate, never retrofit to past winners. The FMP theme taxonomy is also coarse
(memory/AI-hardware fold into semis/hardware), which must be addressed first.

### D6. Should the enrichment cap be separate for the recall lane?
**Yes.** The recall lane should get its **own small enrichment/Lens-prebuild cap**
(e.g. top 15–25 ranked), independent of the Alpha candidate band, so it cannot
crowd the board's enrichment budget and vice-versa. This keeps the two surfaces'
costs and precision contracts decoupled. FMP budget impact is bounded by the small
cap; OHLCV deepening (Task 1) does not touch FMP.

### Sequencing (all gated, none implemented this phase)
1. Deepen cache (Task 1, operator-approved) → re-run coverage + scanner-truth.
2. Accrue RS-lane forward history nightly → `rs_recall_forward_validation`.
3. On `READY_TO_FEED_LENS`: implement D3 (parallel lane → Lens) + D6 (separate cap).
4. Only later, and separately validated: D2 (adaptive gates), D5 (theme boost), D4-B.

---

## Surfacing update (Phase 1G.7)

The forward-validation sidecar
`cache/research/rs_recall_forward_validation_latest.json` and the theme history
feed two new pre-rendered hooks in the aggregate
(`cache/research/scanner_recall_repair_latest.json` → `summary`):
`rs_forward_block` (MCP) and the extended `dashboard_line`. Still sidecar-only; the
dashboard/MCP are not wired, per the "do not overbuild dashboard UI" doctrine.

---

*Re-run anytime: `./scripts/run_research_cycle.sh scanner-recall` (cache-only).
Aggregate sidecar: `cache/research/scanner_recall_repair_latest.json`.*
