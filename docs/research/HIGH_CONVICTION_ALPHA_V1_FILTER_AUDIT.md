# High-Conviction Alpha V1 — Filter Severity, Recall & Emerging-Outlier Audit

**Status:** research-only diagnostic. `HIGH_CONVICTION_ALPHA_V1` is **frozen** —
nothing here changes its weights, gates, dead-horse logic, or classification. The
audit measures whether the filter is over-restrictive and adds a *separate*
`EMERGING_OUTLIER_WATCH` lane so credible pre-profit / turnaround names are surfaced
without diluting the shortlist.

**Artifacts** (regenerate with the runner commands shown):
- `cache/research/high_conviction_filter_audit_latest.json` + `logs/…_latest.txt` — `filter-audit`
- `cache/research/emerging_outlier_watch_latest.json` (+ `emerging_outlier_forward_latest.json`) — `emerging-outlier`
- Ledger (distinct hypothesis): `data/research/emerging_outlier_history.jsonl`

Numbers below are from the latest real scan (market as-of **2026-07-10**, 107 routed
candidates). They move each cycle; treat them as the illustrative baseline.

---

## 1. Core research question — answer

**Does V1 remove low-quality momentum noise while preserving visibility into credible
emerging companies?**

*Removing noise: yes.* All raw score-100 unprofitable relative-strength names are
correctly excluded (FRMM, RXT, AGL, DFTX, CUE, FCEL, SLS …). The 8-name shortlist
(NVO, NVDA, RDDT, AVGO, JPM, HOOD, NTNX, WFC) is entirely profitable with LOW
business-deterioration risk.

*Preserving emerging visibility: no — until now.* V1 is structurally biased toward
mega/large-cap mature profitable companies (Section 4). **Zero** MID/SMALL/MICRO and
**zero** pre-profit-growth names can reach the shortlist. That is correct for a
*precision* list but makes emerging outliers mathematically impossible to surface.
The new `EMERGING_OUTLIER_WATCH` lane restores **controlled recall**: 28 pre-profit
names with multi-factor emergence evidence are surfaced in a separate, higher-risk,
clearly-labelled lane (Section 5).

---

## 2. Gate review matrix

Every V1 gate, its category, latest candidates affected, how many it was the *sole*
reason for, and a recommended status. **No gate is recommended for removal; nothing
is changed from one scan.** The audit's per-gate decomposition reproduces V1's own
reject decision exactly (`gate_decomposition_matches_v1 = True`).

| Gate | Category | Behavior | Affected | Sole reason | Recommended |
|------|----------|----------|:--------:|:-----------:|-------------|
| data_quarantine_or_invalid | Integrity | hard exclude | 5 | 4 | **KEEP_HARD** |
| session_mismatch | Integrity | hard exclude | 0 | 0 | **KEEP_HARD** |
| invalid_security | Integrity | hard exclude | 0 | 0 | **KEEP_HARD** |
| low_data_confidence | Integrity | hard exclude | 0 | 0 | **KEEP_HARD** |
| missing_bars | Integrity | hard exclude | 0 | 0 | **KEEP_HARD** |
| inadequate_liquidity | Integrity | hard exclude | 2 | 1 | **KEEP_HARD** |
| extreme_dilution (≥25%/3q) | Severe | hard exclude | 6 | 3 | **KEEP_HARD** |
| negative_gross_margin | Severe | hard exclude | 5 | 1 | **INVESTIGATE** |
| business_deterioration_high | Severe | hard exclude | 14 | 8 | **KEEP_HARD** (rename) |
| structural_collapse | Severe | hard exclude | 0 | 0 | **KEEP_HARD** |

Soft gates (classification caps, not rejections): `program_burden_of_proof`,
`low_coverage_score_cap` (<0.60), `single_component_dominance_cap` (>0.55),
`extension_state` → quality-but-extended. All **KEEP_SOFT**.

**Only finding warranting future investigation:** `negative_gross_margin` as a
universal hard veto. It is the sole reason for 1 rejection now, but for some
lifecycles (early scale) or sectors (pre-revenue biotech has no meaningful COGS) a
single negative-GM quarter may be temporary. Recommendation is **INVESTIGATE**, not
change — and it must survive multiple dates + median/winsorized analysis before any
V2 proposal (Section 8).

---

## 3. Dead-horse / business-deterioration logic audit

**Finding: V1 already satisfies the preferred multi-signal rule.** `HIGH` requires a
points score ≥ 4, and no single condition contributes more than +2. Therefore:

- ordinary unprofitability alone → not HIGH ✓
- below-MA200 alone → not HIGH ✓ (only +1, and only with a deep 12-month drawdown)
- moderate dilution alone → not HIGH ✓ (15%/3q is +2 → MEDIUM at most)
- multi-quarter revenue decline **plus** dilution **plus** long-term underperformance
  → HIGH ✓

| Condition | Points | Source | Applies to | FP risk | FN risk |
|-----------|:------:|--------|-----------|---------|---------|
| revenue declining >5% (3q) | +2 | fundamentals overlay | all w/ ≥4 quarters | banks (lumpy) | slow bleeds |
| operating margin < −10% | +1 | overlay | non-financials | early scale | — |
| negative gross margin | +1 | overlay | non-financials | pre-rev biotech | — |
| negative FCF (TTM) | +1 | overlay | non-financials | growth reinvestment | — |
| dilution ≥15%/3q | +2 | overlay | all | funded raises | — |
| net debt > 3× revenue | +1 | overlay | **not banks/REITs** | financials (structural) | — |
| RS 252d < −20pp | +1 | scanner | all | deep-value entries | — |
| below MA200 + dd12m ≤ −60% | +1 | scanner | all | turnarounds | — |
| speculative theme + no growth | +1 | scanner | all | — | — |

**Rename recommendation (adopted for user-facing output):** the internal
`dead_horse_risk` shorthand is presented as **`business_deterioration_risk`** in the
Emerging Outlier lane and audit. It is a research risk state, never a factual claim a
business will fail. Internal V1 compatibility is unchanged.

**Known false-positive class — financials.** Generic net-debt and FCF signals do not
fit banks/insurers/REITs. Example: **GS** (Goldman Sachs) is flagged
`business_deterioration_high` and is one-rule-away from the shortlist. The sector
applicability report (Section 6) marks `net_debt` / `free_cash_flow` / `gross_margin`
`NOT_APPLICABLE` for 20 names so they are not read as weakness. Full sector adapters
are recommended **future versioned work**, not a V1 change.

---

## 4. Market-cap & lifecycle bias (the central finding)

Qualification is structurally concentrated in mega/large-cap mature-profitable names.

| Market cap | n | shortlist | rejected | emerging |
|-----------|:--:|:---------:|:--------:|:--------:|
| MEGA | 14 | **5** | 1 | 0 |
| LARGE | 29 | **3** | 1 | 7 |
| MID | 28 | 0 | 7 | 10 |
| SMALL | 30 | 0 | 10 | 10 |
| MICRO | 6 | 0 | 4 | 1 |

| Lifecycle | n | shortlist | rejected | emerging |
|-----------|:--:|:---------:|:--------:|:--------:|
| MATURE_PROFITABLE | 47 | **8** | 2 | 0 |
| NEWLY_PROFITABLE | 8 | 0 | 3 | 0 |
| PRE_PROFIT_GROWTH | 35 | 0 | 6 | **24** |
| TURNAROUND_OR_DECLINING | 17 | 0 | 12 | 4 |

**Every shortlist name is mega/large-cap and mature-profitable.** No MID/SMALL/MICRO
and no pre-profit name can qualify. This is the precision-vs-recall trade in numbers.
The remedy is **not** to lower the shortlist bar — it is the separate Emerging Outlier
lane, which surfaces 24 of the 35 pre-profit-growth names with credible evidence.

---

## 5. Emerging Outlier Watch (new lane, controlled recall)

Admission requires **all** of: (1) fails a conventional profitability standard —
genuinely unprofitable; (2) passes **every** integrity + liquidity gate; (3) ≥2
independent emergence dimensions, ≥1 of them fundamental or balance-sheet; (4)
business-deterioration risk not HIGH; (5) an explicit lifecycle reason. **Price
momentum alone, social attention alone, or a narrative alone are never sufficient.**

Latest: **28 names** (e.g. PTGX, UCTT, GKOS, ARWR, HIMS, MNKD) — all unprofitable,
each backed by combinations of accelerating revenue, strong unit economics (≥50% gross
margin), long funded cash runway, and controlled dilution. It is a **distinct forward
hypothesis** (own ledger `emerging_outlier_history.jsonl`, own verdict) — never merged
with the shortlist or program cohorts. Current forward verdict: **NEED_MORE_DATA**.

Runner: `./scripts/run_research_cycle.sh emerging-outlier`.

---

## 6. Missingness & sector applicability

The audit distinguishes **four/five states** per factor — `PRESENT`,
`NOT_APPLICABLE`, `DATA_QUALITY_FAIL`, `INSUFFICIENT_HISTORY`, `PROVIDER_MISSING`,
`GENUINELY_UNAVAILABLE` — so low coverage is never conflated with weakness. Valuation
(`pe_ttm`/`p_fcf_ttm`) is `GENUINELY_UNAVAILABLE` for loss-makers (no earnings), which
is correct, not a data gap.

**Sector-inappropriate factors flagged:** 20 candidates — `free_cash_flow` (20),
`gross_margin` (20), `net_debt` (16), `profitability` (4) — chiefly financials and
pre-revenue biotech. These are `NOT_APPLICABLE` and must not be scored as negatives.
Recommended future work: bank (FFO/AFFO, leverage), REIT, and biotech (runway /
milestone) adapters as separate versioned modules — explicitly out of scope for V1.

---

## 7. One-rule-away, tail-opportunity & shadow stress

**One-rule-away: 17 names** excluded by exactly one gate —
`business_deterioration_high` (8), `data_quarantine_or_invalid` (4),
`extreme_dilution` (3), `negative_gross_margin` (1), `inadequate_liquidity` (1). For
each, the audit records the exact gate and the lane it would enter without it (all →
IMPROVING_BUT_UNPROVEN or WATCH_FOR_ENTRY). This is the recall-sensitive set to watch
as forward data matures.

**Tail-opportunity metric: NEED_MORE_DATA.** The current rejected set has no matured
forward returns yet; `missed_top_decile_winners`, `missed_2x_winners`,
`median_rejected_return`, and `rejected_downside_rate` populate as the ledgers mature.
This is deliberately honest — the audit must **not** judge a gate by a single scan.

**Shadow stress (diagnostic only — V1 untouched):**
- dead-horse by signal count: ≥2 → 27, ≥3 → 14, ≥4 → 5 names (sensitive — expected).
- coverage cap 0.55 / 0.60 / 0.65 → 4 / 4 / 4 (robust; not cutoff-dependent).
- dilution band ≥20 / 25 / 30% → 6 / 6 / 4 (the 25% floor is not a knife-edge).

Tests assert these stress computations never mutate production config.

---

## 8. Pre-registered promotion rules (V2 gating)

V1 stays frozen. A future `HIGH_CONVICTION_ALPHA_V2` may soften a gate **only** when
it: (1) repeatedly blocks credible winners across multiple independent dates; (2)
survives median **and** winsorized analysis; (3) is not driven by one sector or a few
extreme names; (4) preserves downside control; (5) improves the shortlist or emerging
lane out-of-sample; (6) is versioned and pre-registered before forward evaluation. The
one current candidate for eventual investigation is `negative_gross_margin` as a
universal veto — flagged INVESTIGATE, not changed.

---

## 9. Architecture critique

- **Precision is genuinely high; recall was zero for emerging names** — now handled by
  a separate lane rather than by weakening the shortlist. This preserves the mandate:
  high precision in the shortlist, controlled recall in the outlier lane.
- **The generic fundamental model is sector-blind.** Financials/REITs/biotech are the
  main false-positive source for deterioration and the main NOT_APPLICABLE source.
  Sector adapters are the highest-value future work.
- **Forward evidence is immature.** Every verdict that depends on returns
  (tail-opportunity, cohort comparison, lane validation) is honestly NEED_MORE_DATA.
  No rule may change before that matures.
- **All exclusions are explicit and measured** — one-rule-away, gate matrix, and
  missingness states mean every rejection has a documented reason, and every missed
  opportunity will be quantified rather than hidden.
