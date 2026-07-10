# Production Research Engine — Final Validation Report (Phase 5)

**Date:** 2026-07-10
**Role:** Principal Quantitative Research Director (validation only — no optimization)
**Status:** RESEARCH-ONLY. No production threshold, ranking, score, filter, gate, or promotion rule was changed during this phase.
**Reproducibility:** `./scripts/run_research_cycle.sh research-programs` regenerates the program-level evidence (`cache/research/research_program_validation_latest.json`); the pre-registered verdict gates live in `research/research_programs.py` and must not be tuned to the data.

---

## 1. Executive summary

The production research engine was validated as a whole — discovery, ranking, labeling, forward measurement, benchmarks, data quality, and the audit/feedback loop — against the question: **does it identify candidates that generate repeatable excess returns?**

The honest answer today: **no program can be validated yet, and none can be condemned yet.** The single most important finding is architectural, and it confirms the phase's premise:

> **The engine has been generating swing- and long-term-horizon hypotheses while measuring itself almost exclusively with 5–10 day evidence, against the wrong benchmark, from a single three-week regime window, with ~29% of observations silently unresolved.**

Under those conditions neither "the engine works" nor "the engine has no edge" was ever a supportable conclusion. Phase 5 fixes the *measurement architecture* (three research programs, horizon-aligned evaluation, pre-registered verdict gates) so that a trustworthy conclusion becomes reachable as evidence matures.

**Per-program verdicts (pre-registered gates):**

| Program | Holding period | Verdict | Why |
|---|---|---|---|
| Tactical | 5–15 td | **INSUFFICIENT_MATURE_EVIDENCE** | 5d/10d matured but only 6–14 distinct dates vs 15 required; early signal flat-to-negative |
| Swing | 45–60 td | **INSUFFICIENT_MATURE_EVIDENCE** | Primary horizons 30/45td **not collected at all**; 60td first matures ~2026-09-09 |
| Long-Term | 6–18 mo | **INSUFFICIENT_MATURE_EVIDENCE** | No primary horizon collected; earliest diagnostic (60td) matures Sep 2026 |

No program earns VALIDATED_EDGE. No program has yet earned NO_EVIDENCE_OF_EDGE — the horizons that would justify that verdict have not been evaluated.

---

## 2. The evidence base (what we actually have)

Ledger: `data/research/research_watchlist_history.jsonl`

- 1,876 rows → **484 deduped episodes** (first appearance per ticker+label; duplication factor ×3.88)
- 350 unique tickers over **22 appearance dates** (2026-06-15 → 2026-07-10)
- Matured 5d: 800 rows = 190 tickers over **14 dates**
- Matured 10d: 371 rows = 118 tickers over **8 dates** (2026-06-15 → 06-24)
- Matured 20d/60d: **zero** (first 20d resolutions ~2026-07-14; first 60d ~2026-09-09)

Regime context of the entire matured window (2026-06-15 → 07-09): SPY −0.4%, QQQ −2.8%, IWM +0.9%. **All matured evidence comes from one flat-to-down window.** Nothing measured so far tells us how the engine behaves in a sustained uptrend or a real drawdown.

---

## 3. Headline result: selection quality at the horizons we can measure

Deduped episodes (first appearance per ticker), 10-day forward excess, date-clustered bootstrap 95% CI:

| Benchmark | Mean excess | Median | Win rate | 95% CI (clustered) |
|---|---|---|---|---|
| vs SPY | −1.32% | −3.32% | 41.5% | [−3.61, +0.96] |
| vs QQQ | **+0.57%** | −2.77% | 45.8% | [−1.53, +1.42] |
| vs sector ETF | −0.24% | −2.29% | 40.8% | [−2.25, +0.49] |
| vs IWM | −2.54% | −5.57% | 39% | — |

Interpretation, honestly stated:

1. **The scary headline (−2.85% vs SPY on all appearances) is mostly a measurement artifact** — duplication inflation (371 rows ≈ 118 independent picks) plus benchmark mismatch (high-beta small/mid picks vs SPY). Deduped and properly benchmarked, the aggregate is statistically **zero**.
2. **But no benchmark shows positive aggregate selection.** Best case (QQQ) is +0.57% with a CI spanning zero; vs the size-appropriate IWM it is clearly negative. Medians are negative everywhere: the typical pick loses to its benchmark, and a few large winners carry the mean.
3. Win rates of 39–46% with strong right skew is a lottery-ticket profile, not a repeatable-selection profile — *at 10 days*. Whether the same picks work at 45–60 days is precisely what has never been measured.

---

## 4. Architecture review — confirmed flaws

**A1. Horizon mixing (the core flaw — CONFIRMED).** One scanner, one label set, one forward yardstick. Labels like ASYMMETRIC_RECOVERY_WATCH (category `long_term_asymmetric`) and SPECULATIVE_10X are explicit multi-quarter theses, yet 100% of the verdicts that shaped operator perception ("NO_FORWARD_EDGE") came from 10-day evidence. Judging an 18-month hypothesis on a 10-day return is scientifically meaningless in both directions.

**A2. The forward tracker cannot measure the Swing program's primary horizons.** Tracker `HORIZONS = [5, 10, 20, 60]`. The Swing program (declared primary focus) evaluates at 30/45/60td — **30 and 45 are not collected**. The Long-Term program's horizons (126–378td) are entirely absent. Tactical's 15d is also missing (10d/20d bracket it acceptably).

**A3. Sample-status overstatement.** The tracker labels 371 matured rows "ROBUST". Those rows are 118 tickers over 8 appearance dates — roughly **8 independent regime observations**. Sample status should be computed from unique tickers × distinct dates, not raw rows.

**A4. Silent resolution attrition → survivorship bias.** Of 520 rows old enough to have 10d returns, **149 (28.7%) never resolved** — dominated by warrants and micro-caps (AACIW, ABVEW, FJET, OLOX…) whose price parquets lack data. The unresolved population is plausibly the *worst-performing* tail, which inflates every measured statistic. Any future verdict must report resolution coverage next to returns.

**A5. Benchmark suite incomplete.** SPY/QQQ/sector exist (Phase 4A.6 — good), but there is no size-matched benchmark. Adding IWM flips the aggregate sign. Verdicts must be stated against a benchmark *set*, never a single index.

**A6. The digest/audit loop propagated a false fact.** The digest prints "matured 20d: not tracked in current artifacts"; the LLM audit turned that into a P0 repair task. In truth the tracker has always collected 20d/60d — they simply had not matured. The feedback loop amplified a wording bug into a misdirected engineering priority. (The real gaps — 30/45d and long-horizon collection, resolution attrition — went unnamed.)

**A7. Ranking score is not predictive at the measured horizon.** Spearman ρ(research_score, 10d excess) = **−0.08** (n=144); score quartiles are non-monotonic (Q1 +1.9%, Q3 −3.4%, Q4 +1.5%). Either the score encodes longer-horizon quality (untested until 45/60d matures) or it is uninformative. Until proven otherwise, score ordering must not be treated as a quality ranking within the board.

**A8. Mild look-ahead in entry pricing.** Returns are computed from the scan-date close — a price the scan itself consumed (nightly runs post-close). Re-basing entries to the next close costs ~0.45pp of 10d mean excess (+0.76% → +0.31%). Small, but it consumes most of the best-case aggregate edge; tactical-program verdicts must use lagged entries.

---

## 5. The 20 validation investigations

| # | Question | Finding |
|---|---|---|
| 1 | Candidate discovery | Recall vs winner universe ~2% (strict funnel); shadow lane 12–14% at 12× funnel recall, 81% FP. Discovery is the engine's weakest measured link. |
| 2 | Ranking quality | FALSIFIED at 10d (ρ=−0.08, non-monotonic quartiles). Retest at 45/60d before redesign. |
| 3 | Score calibration | Scores cluster 65–85 with no forward separation at 10d; treat as un-calibrated until horizon-matched retest. |
| 4 | Benchmark methodology | SPY-only headline misleading; QQQ/sector reduce the gap to ~0; IWM makes it negative. Size-matched benchmark missing. |
| 5 | Forward tracker correctness | Return math verified (close-to-close, trading-day offsets, replicated independently). Correct on what it measures; incomplete in what it measures (A2) and silent about attrition (A4). |
| 6 | Holding-period alignment | MISALIGNED — the central Phase 5 finding (A1). Fixed by the program layer. |
| 7 | Regime dependence | Untestable: one 3-week flat-to-down window. All conclusions are regime-conditional until ≥1 quarter of dates accumulates. |
| 8 | Sector dependence | Matured picks concentrate in Tech/Healthcare; sector-relative excess (−0.24%) ≈ flat, so the SPY gap is largely sector/beta tilt. |
| 9 | Market-cap dependence | Not directly testable (no mcap in ledger). IWM comparison + unresolved-row profile imply strong small-cap tilt. Add mcap to the ledger. |
| 10 | Factor ablation | Not testable without re-running the scanner with components disabled — out of scope for a no-change phase; documented as future experiment. |
| 11 | Social-attention contribution | Weakly positive early read: social-sourced episodes +0.58% mean / +2.49% median / 62% win (n=21) vs QQQ. Not proof; the only category with positive median. |
| 12 | Fundamental overlay contribution | Display-only overlay; contribution untested by construction. The audit's tiering (Phase 4B-era) now enables a future profitable-vs-unprofitable forward split. |
| 13 | Data quality | Active universe fresh (~1,042 parquets <36h); 5,620 total parquets include ~4,600 legacy/stale correctly excluded by guards; median depth ~112 bars; 268 deep-cache names. |
| 14 | Stale-data leakage | None found in resolved returns (resolver requires full windows). Staleness manifests as attrition (A4), not contamination. |
| 15 | Survivorship bias | CONFIRMED via 28.7% unresolved mature-age rows (A4). Direction: inflates results. |
| 16 | Look-ahead bias | Mild, quantified at ~0.45pp/10d (A8). |
| 17 | Duplicate observations | CONFIRMED ×3.1–3.9 inflation; fixed via episode dedupe in all Phase 5 statistics. |
| 18 | Label quality | Heterogeneous — see §6. Labels are real hypotheses with opposite early signs; the flat aggregate hides them. |
| 19 | Recall quality | Strict funnel precise-but-blind (winner recall 2%, precision decent when firing); loose variants add recall; evidence MIXED, one window. Unchanged from 1G.x — no gate change proposed. |
| 20 | Dashboard interpretation | Pre-Phase-5 dashboard implied one universal forward verdict; program routing now exposed via `build_research_programs()` (program, holding period, maturity, verdict per label). TUI/digest wiring is a follow-up. |

---

## 6. Label review (every label = one hypothesis)

Deduped ticker+label episodes, 10d excess vs QQQ (diagnostic for Swing/Long-Term; primary-adjacent for Tactical):

| Label | Program | n | Mean | Win | Early read |
|---|---|---|---|---|---|
| BEATEN_DOWN | Swing | 11 | **+12.6%** | 73% | POSITIVE — survives all four benchmarks (+9.3% vs IWM); ex-top-2 still +6.8%. Small n, 4 dates, one regime. **Most promising hypothesis in the engine.** |
| RISKY | Tactical | 5 | +8.1% | 80% | Too few episodes |
| NO_SOCIAL_DATA | Tactical | 6 | +17.1% | 100% | Too few; likely artifact label (radar coverage gap proxy) — candidate for retirement as a *label*, keep as data-quality flag |
| SPECULATIVE_10X | Long-Term | 13 | +3.7% | 46% | 10d read is meaningless for a 10x thesis; wait |
| SECTOR_LEADER | Swing | 22 | +1.3% | 45% | Flat/mixed |
| EARLY_ACCUMULATION | Swing | 26 | +0.9% | 46% | Flat/mixed |
| ASYMMETRIC_RECOVERY_WATCH | Long-Term | 36 | −1.5% | 39% | Diagnostic only; wait for quarters |
| CATALYST | Tactical | 9 | −4.4% | 22% | Early NEGATIVE at its own horizon — first candidate for NO_EVIDENCE if it persists |
| EXTENDED | Swing | 10 | −4.0% | 20% | Early NEGATIVE — consistent with too_extended block audits |
| WATCH | Tactical | 12 | −9.6% | 33% | Early NEGATIVE — watch-grade social names underperform; correctly not high-priority |
| RS_MOMENTUM_LEADER | Swing | 45 eps | — | — | Zero matured (added late); first reads ~mid-July |
| SOCIAL_ARB | Tactical | 22 eps | — | — | Zero matured at 10d; 5d only |

**Multiple-comparisons honesty:** with 12 labels, the best label's +12.6% must be discounted — under a global null, the max of 12 small-sample means will often look this good. BEATEN_DOWN is a *hypothesis to pre-register and track*, not a validated edge.

---

## 7. What is working / not working / unproven

**Working (validated during this phase):**
- Operational pipeline: 6 timers, clean nightly runs, fresh artifacts, 0 errors.
- Forward measurement plumbing: return math correct; benchmarks (SPY/QQQ/sector) attached; append-only ledgers.
- Self-audit discipline: digest → LLM audit → feedback queue → bookkeeping now closes the loop; the graveyard of killed strategies proves gates are real.
- Guardrails: research-only invariants enforced in code on every path.

**Not working (defects found, fixes are measurement-side only):**
- Horizon alignment (A1/A2) — fixed by the program layer for evaluation; tracker horizon additions (30/45td; long-term horizons; IWM benchmark; mcap field) are required follow-ups.
- Sample-status inflation (A3), resolution attrition (A4), digest 20d wording (A6).
- Ranking score at short horizons (A7) — do not present score order as quality order.

**Unproven (not failed — unmeasured):**
- Swing program (primary focus): first honest verdict possible ~Sep 2026 (60td), full primary coverage requires adding 30/45td collection now.
- Long-Term program: first diagnostics Sep 2026; primary verdicts 2027.
- Social-attention alpha, fundamental-tier alpha, BEATEN_DOWN hypothesis: promising threads, all below floors.

**Retire / redesign candidates (evidence-based, pending maturation):**
- NO_SOCIAL_DATA as a research label (artifact of radar coverage).
- CATALYST, WATCH, EXTENDED get first-in-line NO_EVIDENCE reviews if their negative early reads persist at their program horizons.

---

## 8. Verdict maturation calendar

| Date | Event |
|---|---|
| ~2026-07-14 | First 20d resolutions (Tactical/Swing diagnostics deepen) |
| ~2026-07-24 | 10d evidence reaches 15+ distinct dates → Tactical verdict can leave INSUFFICIENT |
| ~2026-09-09 | First 60td resolutions → first Swing primary-horizon reads |
| +30/45td collection start | Swing primary coverage becomes complete N days after the tracker starts collecting them |
| ~2027-01 → 2027-12 | Long-Term primary horizons (126–378td) mature |

Re-run `research-programs` after each milestone; verdicts update mechanically from the pre-registered gates.

---

## 9. Confidence statement

- HIGH confidence: architecture findings A1–A8 (directly measured), operational health, duplication/attrition corrections.
- MEDIUM confidence: aggregate-zero tactical selection (8–14 date clusters, one regime).
- LOW confidence (explicitly): every label-level sign, positive or negative — small n, one window, multiple comparisons.
- NO confidence claimed: anything about 30d+ horizons — the data does not exist yet.

The engine has earned neither continued faith nor retirement. It has earned **a properly designed measurement**, which it now has. The next verdicts will be trustworthy.

*Research only — nothing in this report is a trade signal or recommendation.*

---

# Phase 5.1 — Architecture Realignment (implemented 2026-07-10)

Phase 5 diagnosed; Phase 5.1 corrected the measurement architecture. No scanner threshold, filter, gate, ranking, score, or promotion rule changed.

## What was realigned

1. **Canonical horizons.** `TRACKED_HORIZONS` moved to `research/research_programs.py` (single source); the forward tracker imports it. Horizon set expanded from `5/10/20/60` to `5/10/15/20/30/45/60/90/126/189/252/378` — every program's primary and diagnostic horizons are now collected (A1/A2 closed).
2. **IWM benchmark** added tracker-wide (`BENCHMARK_RETURNS_V2`), with automatic backfill onto all existing rows (A5 closed).
3. **Sample honesty.** `sample_status` now grades unique matured tickers, with `matured_unique_tickers` / `matured_distinct_dates` / `matured_by_horizon` exported (A3 closed).
4. **Resolution attrition** is now measured and printed with every tracker run (`resolution_coverage`; 10d coverage 71.3% at cutover) (A4 made visible; the repair of the unresolved population remains open).
5. **Digest wording fix.** "matured 20d: not tracked" replaced with real per-horizon counts; 45d/60d added to the line (A6 closed).
6. **Candidate routing.** Every ledger row and every new candidate carries `research_program`, holding period, and confidence; the scanner's terminal output prints a program view (program, holding period, hypothesis, status, research-only) for top candidates — candidates never appear as bare tickers.
7. **Per-program surfaces.** Digest section `4b. Research Programs` (per-program candidates/verdict/maturity/diagnostic); journal audit gains `program_verdicts` (digest-grounded, LLM cannot override, `overall_engine` pinned to the safety verdict); command center home shows three program cards (holding period, validation, maturity, lifecycle-bearing label routing); nightly chain runs `research-programs` before the digest.
8. **Research lifecycle.** Deterministic stages — DETECTED → RESEARCH_CANDIDATE → UNDER_OBSERVATION → EARLY_CONFIRMATION → VALIDATED_RESEARCH_CANDIDATE / REJECTED — computed from evidence state per label, exposed in the sidecar and adapter. Never advanced manually or by the LLM.

## Migration notes

- **Ledger:** additive only. New per-horizon fields resolve lazily; IWM + program fields backfilled on first V2 run; all 1,876 pre-existing rows preserved byte-compatible for old readers (new keys only). `resolved` semantics unchanged but now requires all 12 horizons — consumers must use per-horizon fields.
- **Artifacts:** `research_forward_latest.json` gains `resolution_coverage`, `program_counts`, `tracked_horizons_td`, per-bucket unique-ticker fields, and `*_vs_iwm` stats. Old dashboard readers are unaffected (root shape preserved).
- **Digest:** section 4 line changed (matured 20d/45d/60d real counts); new section 4b. The audit's regexes are content-based and unaffected; a new `_PROGRAM_LINE_RE` parses 4b.
- **Sidecars:** `research_program_validation_latest.json` now carries `lifecycle_stage` per label. Absence of the sidecar does not alarm the digest (section 4b reports the rerun command instead).
- **Tests:** legacy fixtures updated for unique-ticker sample basis and `BENCHMARK_RETURNS_V2`; three new per-program audit tests; lifecycle tests. Nothing else migrated.
- **Feedback queue:** the misdirected "add 20d tracking" P0 (spawned by the wording bug) is superseded by this phase — resolve it against the Phase 5.1 commit when reviewed.

## Architecture Critique (self-review — nothing protected)

**What assumptions may still be incorrect?**
- Label→program routing is judgment, not evidence: BEATEN_DOWN as Swing (not Tactical mean-reversion) and SPECULATIVE_10X as Long-Term are defensible but untested assignments; if a label's edge concentrates at a different horizon than its program's primaries, the pre-registered gates will misjudge it. Mitigation: diagnostic horizons exist on every program — watch for primary/diagnostic divergence before re-routing (which must be pre-registered, not reactive).
- Episode = first appearance per ticker+label, forever. With months of history, a ticker re-surfacing after a long gap is genuinely new evidence; the current rule undercounts it. A gap-based episode definition (e.g. >20td absence starts a new episode) should be pre-registered before 60d evidence matures.
- The close-to-close return model ignores dividends and splits (parquet adjustment assumed but unverified for long horizons) — material for 6–18 month Long-Term measurement.

**What remains scientifically unproven?**
- Everything performance-related. No program has met coverage floors; every early read comes from one three-week regime window. The realignment fixed the ruler, not the evidence.

**What would a professional quant team challenge?**
- The verdict gates' specific constants (100 episodes/15 dates, win-rate floors) are round numbers, not power calculations. Defensible as pre-registration, weak as statistics.
- Date-clustered bootstrap handles same-night correlation but not cross-date overlap of multi-week windows (adjacent appearance dates share most of their 45d window); block-bootstrap by window would be stricter. Known and accepted for now.
- Survivorship: reporting attrition is not fixing it. Until unresolved rows are resolved-or-classified, every positive stat is an upper bound.
- One engine feeds all three programs: discovery itself is horizon-blind (labels are assigned post-hoc from one scan). A team would ask whether Long-Term "discovery" is real or a relabeled momentum scan.

**What has become unnecessarily complex / should be simplified?**
- 40+ research sidecars from completed one-shot phases dilute attention; an archive sweep is overdue.
- The tracker now carries two verdict systems (legacy 10d bucket verdicts + program gates). The legacy verdicts should be visibly demoted (kept only for continuity) and eventually removed from operator surfaces.
- Twelve labels across three programs is a lot of hypotheses for ~20 dates of history; consolidation candidates exist (NO_SOCIAL_DATA is a data-quality flag, not a hypothesis).

**What still lacks sufficient evidence?**
- Swing (the declared primary program): first primary-horizon reads ~2026-08-18 (45d) / 2026-09-09 (60d); 30/45d collection starts now, so full primary coverage arrives ~2026-08-21 for 30d cohorts onward.
- Long-Term: no meaningful verdict before 2027. The program exists as measurement scaffolding, not as validated research.

**If starting from scratch?**
- Define programs and horizons first, generate candidates per program second (horizon-aware discovery), measure third. The current system did discovery → labels → measurement → (only now) programs — this phase retrofits what should have been the foundation.
- One ledger with per-horizon fields was right; per-night re-appearance rows were not — an episode table with observation timestamps would have avoided the duplication problem entirely.

## Per-program verdicts after realignment (unchanged, now correctly measured)

Tactical / Swing / Long-Term: **INSUFFICIENT_MATURE_EVIDENCE** — with, for the first time, complete horizon collection, size-matched benchmarks, honest samples, and visible attrition behind each future verdict.
