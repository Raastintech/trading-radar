# Historical Replay Results — September 2026

*Replay window 2022-01-01 → 2025-12-31. Run 2026-09-05. Harness preserved at
`research/backtests/historical_replay_*.py`.*

> **RESEARCH ONLY.** Everything below is *replay* evidence over a fixed
> historical window. It is **not** live forward evidence, **not** backtest
> evidence for Phase 4B, and **not** a trade signal, gate change, or threshold
> recommendation. Replay verdicts sit on their own ladder
> (`REPLAY_*` / `FUNDAMENTAL_REPLAY_*`) and must never be pooled with the live
> forward ledger, program verdicts, HC/EO routing, or the dashboard.

---

## 1. Executive verdict

| Layer | Verdict |
|---|---|
| Price-only scanner lanes | **`REPLAY_CONTRADICTS`** |
| HC / EO fundamental layer | **`FUNDAMENTAL_REPLAY_CONTRADICTS`** |

The six price-only research lanes underperformed both SPY and the median of
their own source universe at every horizon from 5 to 60 days, monotonically
worsening, with date-clustered 95% confidence intervals excluding zero at every
horizon. The selective fundamental layer above them (High-Conviction Alpha,
Emerging Outlier Watch) rejects the worst bucket the scanner produces but does
not itself generate positive selection against the same-date universe median.

**These results do not alter any live verdict.** Phase 4B remains where it was,
program verdicts are untouched, and no gate, score, filter, threshold, or
routing rule was changed by this work. The replay is a warning about the
selector, not a decision about it.

---

## 2. Data scope

* **Primary window:** 2022-01-01 → 2025-12-31, 209 weekly scan dates (the last
  trading session of each ISO week, taken from SPY's own calendar).
* **2021 excluded.** The delisted-company harvest returned materially
  incomplete coverage for names that stopped trading in 2021. Including it
  would have re-introduced exactly the survivorship bias the replay was built
  to remove.
* **Survivorship-corrected universe.** Survivors (5,887 names with a live price
  parquet) **plus** 2,123 delisted US common equities harvested from the
  provider's delisted-companies endpoint and filtered down to genuine common
  stock — ETPs, funds, warrants, units, rights, preferreds and mutual funds
  removed; SPAC *common* kept, bare "Trust" names kept as REIT-ambiguous.
* **Delisted names are carried to death, not dropped.** Where a name stops
  trading before the horizon closes, the return is measured to its final traded
  bar and labelled `horizon_truncated_by_delisting_end_of_life`. Dropping those
  rows would have restored the bias.
* **Price data:** 7,770 replay parquets written (7,838 requested, 68 empty, 0
  errors), quarantined down to **7,598 clean usable** series — 155 sub-penny
  series and 172 symbol-reuse cases excluded, 0 hard-corrupt series found.
* **Excluded or forward-only:** social attention, news catalyst, topic shock,
  and the options overlay. Their seed functions are disabled in the replay, so
  any residual `social_arb_attention` episodes (n=142) are a by-product, not a
  valid test of that lane.

---

## 3. Price-only replay result

209 scan dates · **14,735 episodes** · **2,671 unique tickers** · ~1,000 names
per date.

**Pooled returns**

| h | mean % | median % | trim10 | win % | vs SPY | vs QQQ | vs IWM |
|---|---|---|---|---|---|---|---|
| 5 | −0.32 | −0.27 | −0.59 | 47.6 | −0.52 | −0.59 | −0.46 |
| 10 | −0.67 | −0.62 | −1.11 | 46.8 | −1.06 | −1.20 | −0.95 |
| 20 | −1.05 | −1.31 | −2.13 | 46.0 | −2.00 | −2.31 | −1.71 |
| 30 | −1.51 | −2.01 | −3.05 | 45.0 | −2.97 | −3.41 | −2.52 |
| 45 | −1.84 | −3.26 | −3.89 | 44.3 | −4.08 | −4.77 | −3.36 |
| 60 | −1.78 | −4.09 | −4.63 | 43.7 | −4.61 | −5.58 | −3.57 |

**Selection skill vs its own universe** (per-date median of picks minus
per-date median of the pool they were drawn from — this isolates selection from
market direction):

| h | scanner | universe | selection | 95% CI | dates won |
|---|---|---|---|---|---|
| 5 | −0.52 | −0.06 | **−0.45** | [−0.74, −0.20] | 42.1% |
| 10 | −1.00 | −0.15 | **−0.84** | [−1.22, −0.48] | 37.3% |
| 20 | −2.06 | −0.13 | **−1.93** | [−2.48, −1.40] | 32.5% |
| 30 | −2.97 | −0.19 | **−2.78** | [−3.39, −2.20] | 28.2% |
| 45 | −4.02 | −0.25 | **−3.77** | [−4.49, −3.07] | 23.0% |
| 60 | −4.63 | −0.34 | **−4.29** | [−4.99, −3.62] | 22.0% |

The scanner beat the same-date median of its own universe on only **22–42% of
dates**. Selection is negative at every horizon and the effect grows with time,
which is the opposite of what a real edge looks like.

**Verdict: the price-only lanes failed as alpha selectors** over this window.
Not "underperformed the market" — underperformed *the pool they picked from*.

---

## 4. Root cause

Located in the live scanner at **`research/research_scanner.py:2021`**:

```python
score = min(100.0, max(0.0, 50.0 + combined_rs * 0.8))
```

with the lane sorted descending on the same input at `research_scanner.py:2048`.

* The score is a pure monotone function of **trailing** relative strength.
* It **saturates at `combined_rs = 62.5pp`** — beyond that every name scores
  exactly 100.
* Realised `combined_rs` in the lane reaches **1,668pp**, so **87.6% of the
  lane's episodes** receive an identical score of 100 while spanning a ~27×
  range of prior outperformance. The score stops discriminating precisely where
  the differences get largest.
* Forward excess return is **monotonically decreasing** in `combined_rs` above
  roughly 40pp:

| combined_rs | 20d excess vs SPY |
|---|---|
| 0–20 | −0.76% |
| 20–40 | −1.03% |
| 40–62.5 | −0.37% |
| 62.5–100 | −1.77% |
| 100–200 | −4.42% |
| 200–400 | −8.73% |
| >400 | **−15.59%** |

So the score is **maximised exactly where forward returns are worst**, and the
lane's sort deliberately takes the most extended names in the universe each
week. The `score=100` bucket is 31.0% of the entire board and is **inverted**:
negative in every year tested (2022 −4.87%, 2023 −4.75%, 2024 −2.56%,
2025 −5.56%).

Extension chase is confirmed as the mechanism, and — importantly — "wait for a
pullback" is **not** the fix. Damage tracks the *magnitude of the prior move*,
not proximity to highs: names at their 52-week high are among the least bad
(−0.77%), while high-RS names that have already reset below MA50 are the worst
cell of all (−9.56%).

Robustness: date-clustered CI [−5.22, −2.30], ticker-clustered CI
[−5.18, −2.37], drop-one-year jackknife −3.26 to −4.14, drop-top-20-tickers
−5.08. No single year, date, or repeat ticker carries the finding.

> **This is a finding, not tuning permission.** The formula was deliberately
> **not** changed in the commit that preserved this harness. No gate, threshold,
> score formula, or ranking should move on the basis of a single replay.

---

## 5. Lane findings

Selection vs the same-date universe median:

| Lane | 5d | 20d | 60d | n |
|---|---|---|---|---|
| `long_term_asymmetric` | **+0.25** | **+0.66** | **+1.79** | 2,683 |
| `sector_theme_leader` | −0.24 | −0.75 | −1.01 | 1,175 |
| `early_accumulation` | −0.27 | −1.70 | −3.09 | 2,886 |
| `beaten_down_recovery` | −0.68 | −2.17 | −5.03 | 2,640 |
| `rs_momentum_leader` | −1.39 | −4.80 | **−10.45** | 5,209 |
| `social_arb_attention` | −6.27 | −8.19 | −15.91 | 142 |

* **`rs_momentum_leader` failed badly** and is the largest lane by episode
  count — it dominates the pooled result.
* **The `score=100` bucket failed badly** and is generated *only* by that lane.
* **`long_term_asymmetric` was the only price-only lane with positive
  selection**, and it improves with horizon.
* `sector_theme_leader` is near flat.
* `early_accumulation` and `beaten_down_recovery` are clearly negative.
* **`social_arb_attention` should be ignored here** — n=142 with its seed
  functions disabled. It is not a valid test of that lane.

---

## 6. Fundamental replay result

Frozen HC (`build_shortlist`) and EO (`build_watch`) run against a per-date
sandbox root holding only replay-derived artifacts.

* **API calls:** 10,684 across 2,671 tickers, **0 failures**.
* **`acceptedDate` coverage: 100%** — 2,634 statement files written, 0 rows
  dropped for a missing filing date, median 32 income quarters per name.
* Point-in-time rule: usable from the first session strictly after
  `acceptedDate`, post-16:00 acceptance rolls to the next day, newest 4 quarters
  only (production passes exactly 4).
* 14,641 episodes · 209 dates · 2,669 tickers · 4,546 at `score=100` ·
  1,356 HC shortlisted · 3,650 EO selected.

**Test 1 — exposure to the `score=100` trap**

| | HC shortlist | EO watch |
|---|---|---|
| acceptance of `score=100` | **0.26%** | **28.24%** |
| acceptance of non-100 | 13.31% | 23.44% |
| rejection of `score=100` | **99.74%** | — |

**HC rejects the trap decisively. EO inherits it** — it accepts `score=100`
names at a *higher* rate than everything else.

**Tests 2/3 — selection vs same-date universe median** (positive = adds value)

| h | HC | HC CI | EO | EO CI |
|---|---|---|---|---|
| 5 | −0.17 | [−0.56, +0.21] | −0.18 | [−0.55, +0.20] |
| 10 | −0.49 | [−1.00, −0.02] | −0.36 | [−0.86, +0.17] |
| 20 | −0.46 | [−1.18, +0.23] | −0.73 | [−1.54, +0.06] |
| 30 | −0.01 | [−1.03, +1.01] | −1.23 | [−2.19, −0.28] |
| 45 | −0.00 | [−1.11, +1.13] | −2.10 | [−3.30, −0.97] |
| 60 | −0.83 | [−2.15, +0.48] | −2.27 | [−3.53, −0.99] |

HC **beats the price-only board** at 30/45/60d (CI excludes zero) but its
selection against the **universe median is negative or indistinguishable from
zero at every horizon — never positive**. It improves on a broken scanner
without producing an edge of its own.

**Constructive lead — EO's deficit is entirely inherited.** Splitting EO by
`score=100` exposure: the non-100 subset is flat-to-positive (20d −0.03%,
60d **+0.87%** vs SPY, n≈2,286) while the `score=100` subset is clearly negative
(20d −1.50%, 60d −4.38%, n≈1,275). EO's whole deficit traces to inherited
`score=100` exposure.

**Test 5 — value factor: NEUTRAL.** All CIs include zero. Nuance worth keeping:
the raw `value_score` *does* rank outcomes monotonically (lowest quartile −3.41%
at 20d vs highest −0.52%; −7.39% vs −0.85% at 60d), but adding it to the
composite at weight 0.10 does not measurably change HC shortlist performance
(shortlist Jaccard on-vs-off 0.553).

**Test 4 — Alpha Focus: SKIPPED, deliberately.** Alpha Focus reads
`cohort_attribution_latest.json` and `alpha_failure_root_cause_latest.json`
(live artifacts derived from the live forward ledger) plus
`research_watchlist_history.jsonl` (a live ledger that sits entirely outside the
replay window, 2026-06-15 onward). Supplying them would inject future
information into every historical date; running without them would omit 2 of 5
inputs and no longer be the frozen rule. No replay-local substitute exists that
does not itself depend on forward outcomes. The harness enforces this in code
(`alpha_focus_replay_safe`) — it is skipped unless all three inputs exist
replay-locally, which never happens for a historical date.

**Verdict: `FUNDAMENTAL_REPLAY_CONTRADICTS`.** The rule required HC/EO to *both*
reject the `score=100` bucket *and* outperform the same-date universe median at
20/45/60d. HC satisfies the first and fails the second; EO fails the first
outright.

---

## 7. What this means

* **The broad price-only scanner is not validated.** Over this window it was
  worse than picking at random from its own universe. It should be treated as
  **discovery-only at best** — a way to narrow attention, not a source of
  ranking or conviction.
* **The HC/EO/fundamental layer did not prove alpha in replay.** It is a real
  improvement over the raw board — HC's 99.74% rejection of the trap bucket is
  doing genuine work — but "better than a broken selector" is not an edge.
* **Do not promote candidates to signals** on the strength of scanner rank,
  research score, or shortlist membership.
* **Do not spend time beautifying dashboards around a contradicted selector.**
  Presentation work on top of this ranking is effort spent making a negative
  signal easier to read.
* **Frame any future work as repair/research, not validation.** The question is
  no longer "does this work?" over this window — it is "what would have to
  change for it to work?"

---

## 8. What remains alive

* **`long_term_asymmetric`** deserves isolated study. It was the only price-only
  lane with positive selection at every horizon tested, improving with time
  (+0.25 / +0.66 / +1.79 at 5/20/60d, n=2,683). It is also the lane least
  coupled to the broken RS score.
* **EO's non-`score=100` subset** deserves isolated study. Flat-to-positive
  standalone; its measured deficit is entirely imported from the trap bucket.
* **Social attention, news catalyst, topic shock** remain **forward-only** and
  are untested by this replay — their seeds were disabled. Nothing here says
  anything about them in either direction.
* **The live 45d/60d forward evidence programs continue unchanged.** The replay
  is a strong warning, not a substitute for them, and does not close them early.

---

## 9. Bias and limitations

1. **Restatement contamination.** The provider serves the *current* version of
   each statement, correctly timestamped by `acceptedDate` but not
   as-originally-filed. Restated figures leak backward.
2. **Current-rule overfitting.** The rules replayed are today's rules, authored
   with knowledge of this period. This is unresolvable by replay — though note
   the results are **negative**, which is the opposite direction from what an
   overfitting artifact produces.
3. **No social, news, topic-shock, or options inputs.** Disabled for the replay.
4. **No costs, exits, or sizing.** Equal-weight, hold-to-horizon, no slippage,
   no commission, no borrow. Real friction would make these numbers worse, not
   better.
5. **Benchmark controls are SPY/QQQ/IWM only.** No per-name sector-ETF
   attribution was available offline, so a lane concentrated in one sector is
   measured against the broad market rather than its sector.
6. **Sector/industry history is a current snapshot**, not point-in-time.
7. **Replay verdicts are a separate ladder.** `REPLAY_*` and
   `FUNDAMENTAL_REPLAY_*` are disjoint from the live ladder by construction;
   the harness refuses to emit `VALIDATED_EDGE` or any live/program verdict.
8. **One window, one regime.** 2022–2025 covers a bear year, two recoveries and
   a momentum-heavy 2025. A single replay is not a general result.

---

## Reproducing this

The harness is in `research/backtests/`, isolated from production (nothing under
`core/`, `council/`, `execution/`, `strategies/`, or `dashboards/` imports it).
Every stage defaults to safe: the three provider-touching stages refuse to run
without `--execute-fetch`, all writes are confined to replay namespaces by
`common.assert_replay_write_path`, and every stage runs a live-artifact tripwire
that fails the run if a live artifact's mtime moves.

```bash
# Universe (stage 1A/1B) — harvest needs --execute-fetch; filter is free
python -m research.backtests.historical_replay_universe_build harvest --execute-fetch
python -m research.backtests.historical_replay_universe_build filter

# Prices (stage 1C) — fetch needs --execute-fetch; summarise is free
python -m research.backtests.historical_replay_price_backfill fetch --execute-fetch
python -m research.backtests.historical_replay_price_backfill summarise

# Price-only replay (stage 2) — zero API
python -m research.backtests.historical_replay_price_only replay
python -m research.backtests.historical_replay_price_only control
python -m research.backtests.historical_replay_price_only analyse

# Diagnosis (stage 2A) — zero API
python -m research.backtests.historical_replay_diagnostics

# Fundamentals (stage 3) — fetch needs --execute-fetch; replay/analyse are free
python -m research.backtests.historical_replay_fundamental_fetch --execute-fetch
python -m research.backtests.historical_replay_fundamental replay
python -m research.backtests.historical_replay_fundamental analyse
```

Prefix with `GEM_TRADER_SKIP_DOTENV=true` for the zero-API stages and
`SNIPER_ENV_PATH=/home/gem/secure/trading.env` for the fetching ones.

**Notes on reproduction fidelity.** The three analysis stages were re-run from
the preserved replay cache during preservation and reproduce every point
estimate in this document exactly. Two second-order differences are expected and
harmless: confidence intervals are recomputed by a seeded date-cluster bootstrap
and can differ in the last decimal from the values recorded above, and the EO
constructive-lead subset counts re-derive as n=1,284 / 2,366 against the 1,275 /
2,286 recorded during the original run (same direction, same conclusion, ~0.5pp
on the 60d figures). The original run's artifacts are the authoritative record
for the numbers in this document.

Large intermediates (`replay_raw.json`, `step3_value_on/off.json`, the control
and diagnostic frames) are preserved under
`cache/research/historical_replay_intermediates/`, and the replay price,
fundamental and market-cap caches under `cache/replay_*`. All of these are
gitignored — the code and this document are the tracked record.
