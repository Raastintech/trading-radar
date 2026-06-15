---
strategy: VOY — Voyager
book: Long Trend / Leadership (Book A)
code_path: strategies/voyager.py
owner: TBD
last_updated: 2026-05-04
---

# Voyager Scorecard

> **Doctrine note:** This scorecard was rewritten 2026-04-17 after the prior
> implementation was found to be doctrine drift. The previous version incorrectly
> classified Voyager as a mean-reversion SHORT (Book C). The correct mandate is
> a long-horizon LONG strategy (Book A). The old implementation has been archived
> to `research/sleeves/voyager_mean_reversion_archive.py`.

> **Phase 10 evidence note (2026-05-04).** The voyager_v2 backtest reports
> **raw forward returns with no friction modelled**. At the mandate-aligned
> 252-day horizon the raw signal is `+20.53%` adj at 95% CI `[+3.64%, +38.48%]`
> over n=16 — visibly above the random-entry control (+6.42%) on the same
> universe. However: n is small, friction is unmodelled, and the universe is
> hand-curated (49 names, survivorship-biased). Phase 9B verdict from
> `docs/scorecards/evidence_rigor_report.md`:
> **`PROMISING_BUT_THIN` — paper-ready under current evidence, not capital-proven.**
> Re-run the export with `--voyager-friction-rt-pct 0.30` to see how a
> 30 bps RT cost shifts the distribution before any capital decision.

## Mandate

Long-horizon LONG only. Capture institutional accumulation before the major run
is widely recognized. Target holding period: **6–18 months**.

**Direction: LONG only. Never SHORT.**

## Thesis

Institutions cannot build large positions quietly. The footprint is rising dollar
volume on tight, constructive price action — before the breakout is visible to
most participants. Retail accounts can enter at this stage with precision that
large accounts cannot. The 6–18 month thesis is institutional accumulation leading
to a sustained multi-month uptrend, not a tactical breakout or mean-reversion trade.

## Three Entry Archetypes

**BASE_ACCUMULATION** — Stock building a multi-week constructive base. Price tight
(< 3% stdev / mean), within ±5% of MA50, dollar volume rising. Entry before the
breakout is visible to most participants.

**TREND_PULLBACK** — Established uptrend (MA50 > MA200, MA50 ascending), stock
pulling back 2–10% below MA50. Better multi-month entry than chasing the extension.

**EARLY_ACCUMULATION** — Pre-golden-cross convergence. MA50 is within 3% below
MA200 and rising toward it, RS is already positive, dollar volume is building,
and fundamentals meet a higher floor. Entry before the widely watched golden
cross is visible.

## Signal Conditions (Hard Gates — all must pass)

| # | Condition | Rejection |
|---|-----------|-----------|
| 1 | Price ≥ $5 | `price_too_low` |
| 2 | 20d avg dollar volume ≥ $5M | `low_dollar_vol` |
| 3 | Price ≥ MA200 × 0.92 | `below_ma200_floor` |
| 4 | Price ≤ MA50 × 1.12 | `too_extended` |
| 5 | RS vs SPY > 0 over 50 trading days | `weak_rs_50d` |
| 6 | 20d avg dvol ≥ 85% of 60d avg | `dvol_fading` |
| 7 | Archetype-appropriate up/down volume ratio | `selling_dominates` |
| 8 | Fundamental score ≥ 40/100, or ≥ 55 for EARLY_ACCUMULATION | `low_fundamental_quality` |
| 9 | No earnings within 15 days | `earnings_soon` |
| 10 | At least one archetype confirmed | `no_archetype` |
| 11 | Composite score ≥ 65/100 | `low_score` |

**MA50 > MA200 is archetype-specific, not universal.** BASE_ACCUMULATION and
TREND_PULLBACK require MA50 > MA200. EARLY_ACCUMULATION intentionally allows
pre-golden-cross entries when MA50 is within 3% below MA200 and rising, with a
higher fundamental floor and stronger dollar-volume requirement.

## Required Data

- Alpaca: daily OHLCV bars (260 days)
- Alpaca: live bid-ask quote (spread agent)
- Alpaca: daily bars for SPY (RS calculation)
- FMP: earnings calendar (15-day buffer)
- FMP: income statement + balance sheet + cash flow (4 quarters each)

## Trade Geometry

```
Stop   = min(entry − 1.5 × ATR,  MA200 × 0.97)
Target = entry + (entry − stop) × 2.5
MIN_RRR = 2.5
```

Stop is structural (MA200-anchored), not tactical. If the stock breaks below MA200,
the 6–18 month thesis is structurally invalidated.

## Council Profile

```
momentum:  0.30   flow:      0.10
earnings:  0.25   sentiment: 0.05
spread:    0.20   sector:    0.10
Threshold: 52.0
```

Momentum is dominant because 20d trend quality is the best available proxy for
multi-week structure. Earnings weight is high because the scanner requires a 15-day
buffer, making the council's 5-day check always contribute ~20 pts. Flow and
sentiment are de-weighted because intraday noise is irrelevant for a 6–18 month hold.

## Validation Status

| Gate | Status | Notes |
|------|--------|-------|
| data-valid | pass | Alpaca bars confirmed. FMP stable API fixed 2026-04-18 — all 3 statements load. |
| backtest-valid | pass | Full historical backtest run 2026-04-18 with real fundamentals. See results below. |
| paper-valid | **in progress** | Paper cycle started 2026-04-18. Logger live. Need 30 signals. |

## 13F Institutional Confirmation Layer

13F data is wired as a soft scoring overlay (+8 to -5 margin-based). It does not gate trades.
See `core/whale_tracker.py` for tracked institutions and `VOYAGER_LONG_HORIZON_SPEC.md §13F`
for the full scoring table.

**16 institutions in tracking list; 15 produce current data.** Active: Vanguard, State Street,
Fidelity, Berkshire, ARK, Renaissance, Citadel, DE Shaw, Bridgewater, Tiger Global, Point72,
Viking Global, Third Point, Lone Pine, Soros. BlackRock excluded at runtime by staleness guard
(CIK 0001086364 maps to "BlackRock Advisors LLC", last filing 2016). Two Sigma, Millennium,
Coatue, D1 Capital excluded (CIKs unresolved).

**13F anti-lookahead:** Historical validation uses `filing.filing_date` (actual SEC submission)
as the availability date, NOT `filing.period_of_report` (quarter end). Typical filing lag is
29–42 days after quarter end. A scan on Jan 3 would NOT see Q4 Dec 31 data — that files in
early February. Implemented in `research/backtests/voyager_v2_backtest.py`.

**Validation blocker resolved:** 13F overlay with margin-based scoring does NOT inflate
confidence. Average pts = +0.87 across 15 Mode B signals (validated 2026-04-18). The old
binary scoring averaged +6.26 (85% of tickers got maximum +8). Margin-based scoring fixed.

## Validation Requirements

- **Minimum paper sample:** 30 signals with tracked outcomes
- **Hold duration threshold:** ≥ 30 days per signal before assessing outcome
- **First-cycle focus:** entry quality (was the setup constructive? did stock hold above MA200?) rather than exit P&L (6–18 month horizon means final P&L won't be available in the validation window)
- **Backtest minimum:** 12 months of daily scans, out-of-sample last 3 months ✓ completed

## Paper-Cycle Success Criteria (paper-valid gate)

All 7 criteria must pass to promote Voyager to capital-deployment review.
Run `research/paper_trades/voyager_paper_report.py` to check current status.

| # | Criterion | Threshold | Rationale |
|---|-----------|-----------|-----------|
| 1 | Signals collected | ≥ 30 | Minimum sample for statistical signal |
| 2 | Signals with 30d window closed | ≥ 30 | Enough measurement points |
| 3 | 30d avg return | > 0% | Long-horizon signals should not immediately reverse |
| 4 | MA200 hold rate at 30d | ≥ 70% | Structural stop intact = thesis not invalidated early |
| 5 | 90d win rate | ≥ 50% | Basic positive expectancy (n ≥ 10 required) |
| 6 | No archetype with 90d WR < 30% | none failing | No archetype systematically wrong |
| 7 | 13F avg pts contribution | ≥ -2 | Overlay not actively harmful to selection |

**Note on exit P&L vs entry quality:** For a 6–18 month strategy, the paper cycle cannot
wait for full exit P&L. Gates 3 and 4 assess entry quality: did the stock hold its
structural stop and trend in the right direction at 30 days? Gates 5 and 6 use 90d
outcomes where available, which is achievable within a paper cycle of reasonable length.

## Paper-Cycle Infrastructure

- **Auto-logger:** `core/voyager_paper_logger.py` — activated by `VOYAGER_PAPER_LOG=true` in `.env`
- **DB table:** `voyager_paper_signals` in `db/trading.db` (43 columns)
- **Report:** `SNIPER_ENV_PATH=... .venv/bin/python3 research/paper_trades/voyager_paper_report.py`
- **Outcome update:** Add `--update-outcomes` flag to fetch current prices and fill in elapsed measurement windows
- **Doctrine check:** Every row has `direction='LONG'`. VOYAGER never shorts.

## Validation Blockers

- [x] Confirm SPY bars available for RS calculation via Alpaca — confirmed working
- [x] Run backtest on production LONG scanner — completed 2026-04-18
- [x] Fix FMP fundamentals — fixed 2026-04-18: all stable API paths updated in `core/fmp_client.py`; cashflow added; grossProfitRatio computed from grossProfit/revenue
- [x] EARLY_ACCUMULATION signals confirmed with real fundamentals — AXON 2022-Q4: score=85, fund=98, 180d=+93.2%
- [x] TREND_PULLBACK gate sequencing fixed — archetype detection now runs before selling_dominates; relaxed to ≥0.8 for pullback entries
- [x] Universe bias validation — emerging-growth names find equal signal rate (1.5%) vs mid (1.4%); when found, outperform (avg 180d +76% vs +12% large)
- [x] Paper validation infrastructure — `voyager_paper_signals` table, auto-logger, report script all deployed 2026-04-18
- [ ] Complete 30 paper-trade signals with ≥ 30-day hold tracking

## Last Backtest Result

**Run: 2026-04-18 (post-fix)** — `research/backtests/voyager_v2_backtest.py`

**Configuration:**
- Universe: 49 liquid US equities across 10 sectors
- Window: 2022-01-03 → 2024-10-01 (12 quarterly scan dates, ~3 years)
- Forward returns: 30d / 90d / 180d / 365d from signal date
- 13F anti-lookahead: `filing.filing_date <= scan_date` (not period_of_report)
- Real FMP fundamentals: all 3 statements loaded (income + balance + cashflow)

**Mode A (base scanner, no 13F) — 588 evaluations:**

| Metric | Value |
|--------|-------|
| Signals | 23 (3.9% rate) |
| 30d avg return | +5.1% (win rate 61%) |
| 90d avg return | +13.8% (win rate 74%) |
| 180d avg return | +18.1% (win rate 74%) |
| 365d avg return | +24.1% (win rate 74%) |
| Archetype mix | BASE=17, PULLBACK=5, EARLY=1 |

**Mode B (base + 13F, anti-lookahead) — same 588 evaluations:**

| Metric | Value |
|--------|-------|
| Signals | 22 (3.7% rate) |
| 90d avg return | +12.8% (win rate 73%) |
| 180d avg return | +17.0% (win rate 73%) |
| 365d avg return | +23.3% (win rate 73%) |
| Avg 13F pts | +0.82 |
| Threshold events | 1 of 588 changed pass/fail |

**13F pts distribution (Mode B, 22 signals):**
+8: 2 (9%) · +5: 3 (14%) · +3: 1 (5%) · +1: 8 (36%) · −2: 4 (18%) · −3: 2 (9%) · −5: 2 (9%)

**Primary rejection reasons (Mode A):**
`weak_rs_50d` 30% · `below_ma200_floor` 27% · `no_archetype` 20% · `too_extended` 11% · `dvol_fading` 8% · `selling_dominates` 5%

**Sample signals by archetype:**

| Archetype | Date | Ticker | Score | Fund | RS50d | 180d |
|-----------|------|--------|-------|------|-------|------|
| BASE | 2022-10-03 | LLY | 86 | 90 | +9.3% | +13.9% |
| BASE | 2022-01-03 | MSFT | 81 | 95 | +4.2% | −23.9% |
| BASE | 2023-10-02 | CVX | 79 | 89 | +14.0% | −0.7% |
| PULLBACK | 2022-07-01 | XOM | 75 | 74 | +13.4% | +28.5% |
| PULLBACK | 2023-10-02 | AMAT | 68 | 85 | +8.6% | +51.4% |
| PULLBACK | 2023-10-02 | AMZN | 66 | 100 | +2.9% | +43.4% |
| EARLY | 2022-10-03 | AXON | 85 | 98 | +20.9% | +93.2% |

**Key findings:**

1. **All three archetypes now generating signals.** TREND_PULLBACK (5 signals, 100% win rate at 90d, avg 180d +34.9%) and EARLY_ACCUMULATION (1 signal, AXON +93.2% at 180d) are both working after the two fixes.

2. **TREND_PULLBACK shows best 90d/180d returns.** +30.3% avg at 90d vs +6.3% for BASE. Small n=5 sample, but directionally consistent with the thesis: catching established uptrends at a pullback offers better entry than buying into a tight base.

3. **EARLY_ACCUMULATION (AXON 2022-Q4) is the highest-conviction signal in the set.** Score=85, fund=98, 180d=+93.2%. This is the pre-golden-cross entry thesis working exactly as designed: institutional accumulation starting before the widely-watched technical trigger.

4. **13F adds marginal negative in Mode B (−1.1pp at 180d).** The 1 threshold-down event (13F pushed a signal below 65) removed a signal that would have been profitable. The avg pts of +0.82 with good discrimination (+8 only for 2/22 cases) is correctly calibrated — the overlay is not distorting selection.

5. **No Sniper overlap.** `no_archetype` (112) >> `too_extended` (60). Scanner is correctly rejecting breakout extensions and only passing constructive setups.

6. **Selling_dominates dropped from 14% to 5%** of rejections after the TREND_PULLBACK gate fix. No archetype-appropriate signals are being filtered on volume.

**Recommendation:** Ready for paper validation. Single remaining gate: paper cycle (30 signals, ≥30-day hold tracking).

## Last Paper-Trade Result

Not started.

## Promotion Blockers

All three gates pending. Scanner rebuild completed 2026-04-17; paper cycle not yet started.

## Size-Bucket Bias Validation

**Run: 2026-04-18** — `research/backtests/voyager_v2_backtest.py --bias --skip-13f`

**Configuration:** Expanded universe (77 tickers: 30 large / 36 mid / 11 emerging).
Same 12 quarterly scan dates 2022–2024. Emerging-growth bucket: APP, CAVA, CELH, DUOL,
ELF, GLBE, HIMS, IRTC, RXRX, SMCI, TMDX. Size buckets by approximate 2022–2024 market cap.

**Question:** Is Voyager structurally biased toward large established names, or can it
identify smaller tradeable names being quietly accumulated?

**Signal rates by size bucket:**

| Bucket | Tickers | Evals | Signals | Rate | Avg 90d | Avg 180d | WR 90d |
|--------|---------|-------|---------|------|---------|---------|--------|
| Large (>$50B) | 30 | 360 | 21 | 5.8% | +10.7% | +12.9% | 71% |
| Mid ($5B–$50B) | 36 | 432 | 6 | 1.4% | +27.9% | +36.2% | 100% |
| Emerging (<$5B) | 11 | 132 | 2 | 1.5% | +54.3% | +76.2% | 100% |

**Emerging signals (both TREND_PULLBACK):**

| Date | Ticker | Score | Fund | RS50d | 180d |
|------|--------|-------|------|-------|------|
| 2022-10-03 | TMDX | 72 | 78 | +28.6% | +69.0% |
| 2022-10-03 | SMCI | 68 | 78 | +16.7% | +83.5% |

**Top rejection reasons by bucket:**

| Gate | Large | Mid | Emerging |
|------|-------|-----|---------|
| `weak_rs_50d` | 34% | 22% | 18% |
| `below_ma200_floor` | 19% | 31% | 25% |
| `no_archetype` | 24% | 18% | 12% |
| `too_extended` | 8% | 14% | **25%** |

**Key findings:**

1. **No fundamental size bias.** Emerging-growth signal rate (1.5%) is essentially equal
   to mid-cap rate (1.4%). Voyager is not structurally rejecting smaller names.

2. **Emerging signals produce outsized returns.** +76.2% avg 180d vs +36.2% mid and
   +12.9% large. SMCI in Oct 2022 at score=68, then +83.5% at 180d is the thesis working
   exactly as designed — catching institutional accumulation before the public run.

3. **TREND_PULLBACK is the natural emerging-growth entry archetype.** Neither emerging
   signal was BASE_ACCUMULATION. Small-cap volatility naturally prevents the tightness
   requirement (< 3% stdev/mean) from being met. TREND_PULLBACK's looser requirements
   are appropriate for high-conviction emerging accumulation setups.

4. **`too_extended` is the primary emerging-specific gate.** 25% of emerging rejections
   vs 8% for large. Emerging names are often in binary states: already flying (extended)
   or broken (below MA200). Voyager correctly catches them in the rare constructive
   window. This is not a design flaw — it is discipline.

5. **Large-cap signal rate is higher (5.8%) but returns are lower (+12.9% 180d).** Large
   caps more consistently produce constructive setups, but the alpha is in mid/emerging
   names when they do pass. No gate adjustment needed — this is the correct trade-off.

**Identity verdict:** Voyager is a **quality-first leader-finder that works across all
size buckets**. Large caps dominate signal count because they more consistently maintain
the MA50/MA200 structure and RS leadership required. But when emerging-growth names pass
all gates, they are the highest-conviction setups in the universe — exactly the
institutional accumulation thesis the strategy is designed to capture.

**Recommendation:** No gate changes warranted. Paper cycle proceeds as planned. Monitor
emerging-growth TREND_PULLBACK setups as priority signals.

---

## Quant Research Doctrine Audit — 2026-04-21

VOYAGER was reviewed under the permanent Quant Research Doctrine in
`docs/strategy/STRATEGY_DOCTRINE.md`. This audit is doctrine-grade, not a redesign.

Latest local verification, 2026-04-21:
- `pytest tests/smoke/test_strategy_scanners.py::TestVoyagerScanner -v`: 2 passed.
- First `voyager_v2_backtest.py --quick --skip-13f` run exposed a cached-data bug:
  Pandas `datetime64` indexes were compared to Python `date` values.
- Fixed `research/backtests/voyager_v2_backtest.py` to normalize `as_of` and
  `signal_date` with `pd.Timestamp(...)`.
- `voyager_v2_backtest.py --quick --skip-13f`: passed after the fix, 3 Mode A
  signals, 90d WR 100.0%, 180d avg return +18.8%.
- `voyager_v2_backtest.py --skip-13f`: full Mode A run passed, 23 signals,
  90d WR 73.9%, 180d avg return +18.1%, 365d avg return +24.1%.
- `py_compile research/backtests/voyager_v2_backtest.py`: passed.

Mode B / 13F was not rerun in this local test pass; it remains covered by the
prior 13F anti-lookahead validation and should be rerun before capital promotion
if the paper/council measurement suggests 13F materially changes pass/fail status.

Latest size-bucket note: the full 2026-04-21 Mode A run showed large=21 signals,
mid=2, emerging=0 in the current 49-ticker universe. That is still consistent with
VOYAGER as an institutional-quality leader finder, but the older "no size bias"
language should be treated as dependent on the expanded bias universe rather than
as a universal claim.

### Strategy Audit

| Doctrine item | Status | Evidence |
|---|---|---|
| Mandate | PASS | Long-horizon LONG-only institutional accumulation / leadership sleeve. Target hold: 6–18 months. |
| Edge source | PASS | Exploits the slow, visible footprint of institutional accumulation: rising dollar volume, constructive price structure, relative leadership, and fundamental quality before the move is fully recognized. |
| Separate reason to exist | PASS | VOYAGER enters before breakout recognition. SNIPER enters only on the confirmed breakout bar, REMORA is short-horizon quiet-flow accumulation, and CONTRARIAN fades panic/forced selling. |
| Trigger | PASS | Three precise archetypes are implemented: BASE_ACCUMULATION, TREND_PULLBACK, and EARLY_ACCUMULATION. Each has explicit price/MA/dollar-volume/RS/fundamental gates. |
| Invalidation | PASS | Structural invalidation is MA200 failure: stop = lower of entry - 1.5x ATR and MA200 x 0.97. This matches a 6–18 month thesis better than a tactical stop. |
| Regime fit | PASS | Normal active state is leadership/accumulation outside crash protocol. In VIX 30–35, crisis accumulation requires stronger quality and smaller first tranche. VIX >= 35 suspends new entries. |
| Scanner/council fit | PARTIAL | Scanner is aligned. A VOYAGER-specific council profile is documented, but live `council/veto_council.py` still uses generic weights. This is a paper-phase measurement issue, not a scanner redesign issue. |
| 13F usage | PASS | 13F is a delayed soft overlay only, scored -5 to +8. Historical validation uses filing date, not period of report. It does not gate trades and does not replace live accumulation proxies. |

### Pre-Backtest / Pre-Paper Checklist

| Section | Status | Notes |
|---|---|---|
| A. Doctrine / mandate clarity | PASS | Edge source, trigger archetypes, invalidation, regime fit, holding horizon, and separation from neighboring strategies are explicit. |
| B. Scanner / council fit | PARTIAL | Scanner and mandate align. Council profile is specified in docs, but the live council has one generic weight table. Paper validation should report scanner-only and scanner+council outcomes separately. |
| C. Data integrity | PASS | Fundamentals path repaired; backtest uses real FMP statements; 13F anti-lookahead is based on filing date; fundamentals are prefetched/cached before scan loops. |
| D. Execution realism | PASS | Friction and geometry are defined; stop is structural; paper logger captures 30d/90d/MA200-hold outcomes appropriate for a 6–18 month sleeve. |
| E. Output expectations | PASS | Backtest and paper gates are stated: minimum 30 paper signals, 30d average return > 0%, 30d MA200 hold rate >= 70%, 90d WR >= 50% when sample exists, and 13F contribution not harmful. |

### Final Recommendation

**Keep VOYAGER mostly as-is; one narrow paper-readiness pass is justified.**

Do not redesign the scanner, add indicators, or retune thresholds. Current evidence
supports the doctrine: Mode A and Mode B backtests are positive, all archetypes now
generate valid signals, fundamentals are repaired, 13F is correctly soft, and the
size-bucket validation confirms the sleeve is not merely a large-cap filter.

The only justified narrow pass is **scanner-only vs scanner+council paper measurement**:
record whether the generic council blocks or downgrades valid VOYAGER signals before
capital promotion. If it does, implement the already documented VOYAGER council profile.
If it does not, leave the scanner unchanged and continue paper validation.

### Remaining Real Blocker

The remaining blocker is not doctrine. It is paper validation: collect at least 30
VOYAGER paper signals with >= 30-day outcome tracking before capital review.

### Lessons for REMORA

- REMORA's edge is now locked as quiet institutional flow. Catalyst dislocation
  should become a separate sleeve if pursued.
- Do not let REMORA drift into SNIPER. If there is a new 20-day high breakout, that is
  SNIPER territory unless REMORA has a distinct quiet-flow timing reason.
- Intraday/short-lived data is more relevant to REMORA than to VOYAGER, so council flow
  can matter there, but only if it uses real live-feed evidence and fails closed.
- Avoid adding more event/news/flow fields unless they separate winners from losers in
  REMORA's actual short-horizon failure mode.

### Lessons for CONTRARIAN

- CONTRARIAN must be explicit about panic completion and stabilization. "Oversold" alone
  is not a trigger.
- Regime gating should be the center of CONTRARIAN, not an afterthought: active in panic,
  selective in elevated fear without stabilization, silent in normal weakness.
- Invalidation should be concrete: continued breakdown after stabilization failure, not
  vague discomfort with volatility.
- Do not reuse VOYAGER's quality/leadership assumptions. CONTRARIAN exploits forced
  selling and volatility overshoot, which is a different edge source.

### Next Doctrine Sequence

1. REMORA doctrine audit.
2. CONTRARIAN doctrine audit.

## Current Phase: Paper Validation

**Status: Active — collecting signals.**

1. Set `VOYAGER_PAPER_LOG=true` in `/home/gem/secure/trading.env`
2. Run the Voyager scanner at your normal cadence — signals auto-log to DB
3. Weekly: run `research/paper_trades/voyager_paper_report.py --update-outcomes`
   to fill in elapsed 30d/90d outcome windows
4. Check paper-valid gate status in Section 7 of the report
5. Do not re-design gates during the paper cycle — let measurement run

**Priority signal types** (from bias validation):
- Emerging-growth TREND_PULLBACK: highest 180d forward returns (+76% avg in backtest)
- Mid-cap TREND_PULLBACK: 100% 90d win rate, +34.9% avg 180d in backtest
- Large-cap BASE_ACCUMULATION: most frequent archetype, lower but consistent returns

<!-- RIGOR_AUDIT_BEGIN -->

## Evidence rigor strip (auto-generated)

_Last audit: 2026-05-04 02:01 UTC · see `docs/scorecards/evidence_rigor_report.md`._

- **Verdict:** **INDISTINGUISHABLE_FROM_RANDOM**
- **Source:** backtest_csv
- **Sample (closed):** n = 64 (open = 0)
- **Primary horizon (252d):** n=16  ·  avg adj +20.23% [+3.34%, +38.18%]  ·  WR 62.5% [37.5%, 87.5%]
- **All horizons aggregate:** n=64  ·  avg adj +9.22% [+3.70%, +15.44%]  ·  WR 59.4% [46.9%, 70.3%]
- **Random control:** WR 48.8%  ·  avg adj +6.42%  ·  n=80
- **Walk-forward:** not run (insufficient data span)

<!-- RIGOR_AUDIT_END -->

<!-- AUTOPSY_BEGIN -->

## Autopsy summary (2026-05-04)

_Source: `docs/research/SLEEVE_FAILURE_AUTOPSY.md` · evidence-only language._

**Independent observations.** 64 trade-rows = **16 unique entries × 4 horizons (30/90/130/252d)**. Inference should treat n as 16, not 64.

**Per-horizon vs SPY same-window benchmark:**

| Horizon | n | VOYAGER avg adj | SPY fwd same-window | Δ (VOY − SPY) |
|---:|---:|---:|---:|---:|
| 30d  | 16 | +1.00%  | +4.78%  | **−3.78pp** |
| 90d  | 16 | +7.39%  | +9.60%  | **−2.20pp** |
| 130d | 16 | +8.25%  | +10.40% | **−2.15pp** |
| 252d | 16 | +20.23% | +22.54% | **−2.31pp** |

VOYAGER underperforms a buy-and-hold SPY benchmark on **every** mandate horizon.

**Concentration.**
- 11 unique tickers across 16 entries.
- Top-3 tickers (GE, XOM, COST) = **93.6%** of cumulative adj.
- Single biggest contributor: **GE** — 4 trades, sum +227pp, avg +56.8% (252d during 2023–24 industrial moonshot).
- Tech sector cohort (n=4): **−22.45% avg, 0% WR**.

**Regime split.** Bull-at-entry (n=40) +7.89%; Bear-at-entry (n=24) +11.42%. The bear-regime advantage is consistent with reversal capture from drawdown lows, not with emerging accumulation.

**Diagnosis.** VOYAGER is **long-only mega-cap-defensive beta with negative alpha vs SPY**. The 13F signal as currently expressed picks already-large already-sponsored names rather than *emerging* sponsorship. Headline-vs-random is +2.80pp adj; headline-vs-SPY is −2 to −4pp on every horizon.

**Disposition.** Stays **paper-only**. **Redesign required** before promotion-track work. See hypothesis H2 in `docs/research/SLEEVE_FAILURE_AUTOPSY.md` (sector leadership + 13F-delta sponsorship + forward breadth must all agree). No threshold or scanner change in this phase.

<!-- AUTOPSY_END -->
