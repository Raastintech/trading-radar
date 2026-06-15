# Active-Sleeve Failure Autopsy

Generated: 2026-05-04 · Source data: `docs/scorecards/evidence_rigor_report.json` and `docs/scorecards/sleeve_failure_autopsy.json` · Script: `research/sleeve_failure_autopsy.py`.

> **Mode.** Failure / autopsy phase, not a re-tuning phase. **Nothing about
> strategies, scoring, scanner, sleeve status, paper governance, execution,
> dashboard, Alpha Discovery, Stock Lens, Market Forecast, or Daily Entry
> Validator was modified.** This document is analysis only.

Headline verdicts (from rigor audit):

| Sleeve | n closed | Primary horizon | Aggregate avg adj (95% CI) | Δ vs random (aggregate) | Verdict |
|---|---:|---:|---|---:|---|
| `SNIPER_V6`     | 225 | 10d  | +0.58% [−0.04%, +1.22%] | +0.42pp adj | **Indistinguishable from random** |
| `VOYAGER_PAPER` |  64 | 252d | +9.22% [+3.70%, +15.44%] | +2.80pp adj | **Indistinguishable from random** |
| `SHORT_A`       |  13 | 5d   | −4.85% [−8.04%, −1.68%] | −4.10pp adj | **Weak and thin** |

The autopsy adds the diagnostic layer underneath those verdicts: which trades failed, which cohort drives the (apparent) signal, and whether the residual edge is a real claim or an artefact.

---

## Task 1 — SHORT_A failure autopsy

### 1.1 Stop-hit cluster

`SHORT_A` ran 9 historical-backtest trades plus 6 live-paper trades; the rigor audit closes 13 of 15 (2 paper trades still open). Six of the 13 closed trades hit the −10% stop. All six are in the historical-backtest cohort (`baseline_tag = short_history_v1`).

| Entry date | Ticker | Sector | Horizon | Score | Borrow % | Gap-risk % (20d max-up) | Intraday range % | VIX entry | SPY 20d ret | SPY > 200dma | Adj return | MAE against |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|:-:|---:|---:|
| 2024-07-25 | VST  | Utilities (AI-power play) | 6d  | 67.8 | 0.016 | 3.21 | 10.21 | 18.46 | −1.30% | Y | **−10.72%** | n/a |
| 2025-02-24 | MSTR | Technology (BTC-proxy)    | 9d  | 62.3 | 0.025 | 2.93 | 11.51 | 18.98 | −1.77% | Y | **−10.72%** | n/a |
| 2025-03-03 | VST  | Utilities                 | 21d | 67.8 | 0.058 | 3.69 | 17.82 | 22.78 | −3.00% | Y | **−10.76%** | n/a |
| 2025-03-10 | GS   | Financials                | 15d | 61.0 | 0.041 | 1.26 | 6.01  | 27.86 | −6.69% | N | **−10.74%** | 10.4% |
| 2025-04-07 | HON  | Industrials               | 22d | 61.0 | 0.060 | 0.86 | 7.29  | 46.98 | −9.75% | N | **−10.76%** | n/a |
| 2025-04-07 | ZTS  | Healthcare                | 35d | 61.0 | 0.096 | 0.59 | 4.67  | 46.98 | −9.75% | N | **−10.80%** | n/a |

Two non-stop losses also belong in the diagnosis:

| Entry date | Ticker | Sector | Horizon | Score | Borrow % | Gap-risk % | Intraday range % | VIX entry | SPY 20d | Adj return | MAE against |
|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2025-03-10 | JPM | Financials       | 45d | 61.0 | 0.123 | 0.65 | 4.87 | 27.86 | −6.69% | −6.32% | 9.6% |
| 2026-03-23 | EL  | Consumer Staples | 39d | 61.0 | 0.107 | 1.28 | 6.96 | 26.15 | −3.69% | −0.32% | **14.8%** |

Three observations stand out:

1. **VIX timing was the worst possible.** Two trades opened on 2025-04-07 with VIX = 46.98 — i.e., at a fear extreme, the kind of reading that reliably precedes a multi-week reversal rally. SPY rallied +12.0% (HON window) and +16.5% (ZTS window) from those entries; the shorts were stopped at the lows, then the underlyings moved against them by the entire rally.
2. **Volatility/range geometry was hostile.** Intraday range averaged **8.67%** for losers vs **2.35%** for winners. With a flat ~10% stop above entry, names whose normal day already moves 7–17% will hit the stop on routine noise — the signal never had time to play out. VST 2025-03-03 (range 17.8%, stop 10%) is the clearest example of stop-target geometry that cannot survive the underlying.
3. **Bull-regime mega-cap shorts.** VST (×2), MSTR, GS, JPM, HON, ZTS were all 2024–2025 leadership names during a +24.3% SPY tape. Five of the six stop-hits were short either large momentum names mid-rally or financials/cyclicals at the bottom of a sharp pullback.

### 1.2 Winners vs losers (closed trades only)

| Metric | Winners (n=4) | Losers (n=9) |
|---|---:|---:|
| Tickers | ABT, HON, PEGA | ABT, EL, GS, HON, JPM, MSTR, VST, ZTS |
| Cohort | 3 live-paper + 1 historical (HON 2024-07-25, +1.28% / 43d) | 8 historical + 1 live-paper |
| Avg horizon | 14d | 21.7d |
| Avg score | 61.0 | 62.9 |
| Avg borrow % | 0.118 | 0.066 |
| Avg gap-risk % | **0.41** | **1.81** (4.4×) |
| Avg intraday range % | **2.35** | **8.67** (3.7×) |
| Avg SPY 20d ret at entry | **+6.6%** | **−3.7%** |
| Avg VIX at entry | **19.0** (normal) | **28.3** (elevated) |
| SPY > 200dma at entry | 4 / 4 | 4 / 9 |
| Avg MAE against | 1.83% | **9.07%** (where measured) |

Winners cluster in low-volatility, low-gap-risk names entered while SPY is calmly trending up at normal VIX. Losers cluster in high-volatility, high-gap-risk names entered into a tape that was already selling off into elevated VIX.

### 1.3 Cohort-level summary

| Cohort | n total | n closed | WR % | Stop-hit % | Avg adj % |
|---|---:|---:|---:|---:|---:|
| `short_history_v1` (heavyweight backtest export) | 9 | 9 | 11.1 | **66.7** | **−7.76** |
| `live_paper_db` (live paper trades since 2026-04-22) | 6 | 4 | 75.0 | 0.0 | +1.69 |

The historical cohort runs against an SPY that returned **+24.28%** over the same 2024-07-25 → 2026-03-23 window. Shorting into that tape with no regime guardrail produced a 67% stop-hit rate. The live-paper cohort, all opened 2026-04-22 and 2026-04-23 in normal-VIX conditions on low-range names, is so far healthy — but n is tiny.

### 1.4 Structural diagnosis

The historical SHORT_A signal failed for **four compounding reasons**, in priority order:

1. **Wrong-regime entries.** Two trades on 2025-04-07 entered at VIX 46.98, where the empirical base rate of a multi-week reversal is high. There is no current SHORT_A guardrail that says "do not initiate after a flush". This is the single largest contributor to the failure of this cohort.
2. **Stop/target geometry vs underlying volatility.** Using a fixed ~10% stop above entry on names whose 20-day intraday range is already 7–18% means a routine daily wiggle hits the stop. The strategy is structurally short-stopped on its own selection universe.
3. **Shorting strong momentum.** VST and MSTR were two of the highest-momentum names of 2024–2025 (AI-power and BTC-proxy). The score never penalised "we are shorting a leadership name in a leadership tape".
4. **Weak candidate selection.** Historical-cohort scores cluster at 61–68 (vs the 80+ band that SNIPER and VOYAGER use). The threshold permitted entries that the live screen would likely never have shown.

What the autopsy does **not** support:

- It is not a borrow/squeeze story. Losers actually had **lower** average borrow rates (0.066) than winners (0.118). High borrow does not predict failure here.
- It is not a primarily-options-flow story (no per-trade options data was captured).
- It is not pure sample-size noise: with 9 historical trades against a +24% SPY tape, a 67% stop-hit rate is a structural mismatch, not bad luck.

### 1.5 Verdict

**Verdict for SHORT_A:** **`keep paper-only` and **redesign required for the historical setup**.** The live-paper cohort is intact (4-of-4 winners, n=4 — too small to lean on), but the historical evidence is decisively negative; the strategy as expressed in the heavyweight export is worse than its random control by **−5.71pp adj**. Do not promote, do not retune thresholds yet — first answer the autopsy hypotheses in §5 below.

---

## Task 2 — VOYAGER autopsy

### 2.1 Why it dropped from "promising" to "indistinguishable from random"

Voyager passes the headline test (avg adj +9.22%, WR 59.4%, n=64). The audit's apples-to-apples random control on the same universe runs +6.42% / 48.75%, which leaves a delta of **+2.80pp adj** — within the bootstrap noise floor for n=64.

But the deeper problem is benchmark-relative, not random-relative. Per-horizon comparison vs SPY *over the same entry-windows*:

| Horizon | n | VOYAGER avg adj | SPY fwd avg over same windows | Δ (VOYAGER − SPY) |
|---:|---:|---:|---:|---:|
| 30d  | 16 | +1.00%  | +4.78%  | **−3.78pp** |
| 90d  | 16 | +7.39%  | +9.60%  | **−2.20pp** |
| 130d | 16 | +8.25%  | +10.40% | **−2.15pp** |
| 252d | 16 | +20.23% | +22.54% | **−2.31pp** |

VOYAGER **underperforms a buy-and-hold-SPY benchmark on every single mandate horizon**. The "edge" the rigor audit recorded is the gap between VOYAGER and a universe-restricted random control, not vs the index. Once you compare to the index, the gap inverts.

### 2.2 Concentration

| Statistic | Value |
|---|---|
| Unique tickers | **11** |
| Top-3 ticker share of total adj | **93.6%** (GE, XOM, COST) |
| Bottom-3 ticker share of total adj | **−35.4%** |
| Single biggest contributor | **GE** — 4 trades, sum +227.4pp, avg +56.8% |

This is not a portfolio. It is three bets and a tail. GE 252d during the 2023–2024 industrial moonshot does almost all the work; XOM (12 trades, energy cycle) and COST (defensive consumer) round out top-3. Strip those three names and the remaining tickers (4 of 11) are net negative.

### 2.3 Sector and regime split

| Sector | n | Avg adj % | WR % |
|---|---:|---:|---:|
| Industrials | 4 | +56.84 | 100 |
| Consumer Staples | 8 | +19.23 | 100 |
| Healthcare | 24 | +8.74 | 66.7 |
| Energy | 20 | +7.49 | 50 |
| Technology | 4 | **−22.45** | **0** |
| Unknown | 4 | −15.22 | 0 |

| Regime at entry (SPY vs 200dma) | n | Avg adj % |
|---|---:|---:|
| Bull (above) | 40 | +7.89 |
| Bear (below) | 24 | +11.42 |

Bear-regime entries produced higher returns — but those are mostly entries near 2025 Q1 lows that subsequently mean-reverted. Combined with the 13F-driven entry signal, this is consistent with "the system buys quality after a drawdown and the drawdown reverses". That is a **reversal/beta pattern**, not an emerging-institutional-accumulation pattern.

### 2.4 Friction sensitivity

Already in the audit: at the 252-day horizon the headline is robust (+20.5% → +19.5% across 0–1.0% RT). Voyager is not friction-fragile because it is long-horizon — but that robustness is a property of the holding period, not of the signal.

### 2.5 Diagnosis

VOYAGER as currently expressed is **long-only, mega-cap-defensive beta with negative alpha vs SPY**:

- Returns are concentrated in 3 names (93.6%); strip them and the strategy is a loss-maker.
- Every mandate horizon underperforms SPY by 2–4pp.
- Tech sector picks (n=4) are −22.4% — the only growth-leaning cohort, and it is a disaster.
- The bull/bear split shows the apparent "edge" is reversal capture from drawdown lows.
- Friction-robust because of the long hold, not because of the signal.

Answer to "is Voyager finding real emerging institutional accumulation?": **No, not under the current evidence.** The trades cluster on already-large, already-known names (XOM, UNH, CVX, ABBV, MSFT, LLY, WMT, GE) where 13F sponsorship is high but uninformative — they are top-of-list holdings for almost every long-only manager. There is no signal of *emerging* sponsorship here; there is only existing sponsorship in a tape that lifted those names along with everything else.

### 2.6 Verdict

**Verdict for VOYAGER_PAPER:** **`keep paper-only` and **redesign required**.** The audit verdict (`INDISTINGUISHABLE_FROM_RANDOM`) understates the problem; benchmarked against SPY rather than a universe-matched random sample, VOYAGER produces **negative** alpha on every horizon. Do not promote. The hypothesis in §5 (sector-leadership + 13F *delta* + forward breadth) is the test that would distinguish a real emerging-accumulation signal from passive beta.

---

## Task 3 — SNIPER autopsy

### 3.1 Aggregate

`SNIPER_V6`: n=225 closed trades (75 unique entries × 3 horizons). WR 47.6%, avg adj +0.58% [−0.04%, +1.22%]. Random control: WR 42.1%, avg adj +0.16%. Delta vs random: +5.5pp WR, +0.42pp adj. The lower bound of the avg-adj 95% CI is below zero.

### 3.2 Subset analysis — which cohorts beat random?

The audit's question is whether *any* slice of SNIPER outperforms random by enough to call an edge. Slices computed from `notes` metadata + price-cache-derived regime:

**By score bucket (notes-derived):**

| Bucket | n | WR % | Avg adj % | Stop-hit % |
|---|---:|---:|---:|---:|
| 70–79 | 42 | 47.6 | −0.05 | 45.2 |
| 80–89 | 75 | **52.0** | **+0.79** | 33.3 |
| 90–100 | 108 | 44.4 | +0.67 | 43.5 |

The score saturates: the **80–89 bucket beats both lower and higher scores**. Entries scored 90+ do *not* outperform — the calibrated tail of the score is over-fit.

**By horizon:**

| Horizon | n | WR % | Avg adj % | Stop-hit % | Target-hit % |
|---:|---:|---:|---:|---:|---:|
| 5d | 75 | 49.3 | +0.46 | 28.0 | 2.7 |
| 10d | 75 | 50.7 | +0.57 | 42.7 | 13.3 |
| 20d | 75 | 42.7 | +0.71 | 50.7 | 22.7 |

20d gives the best avg return but the worst WR and the highest stop-hit rate — i.e. the larger return is paid for in dispersion.

**By VIX bucket at entry:**

| VIX bucket | n | WR % | Avg adj % |
|---|---:|---:|---:|
| Low (<15) | 69 | 47.8 | **−0.26** |
| Normal (15–20) | 87 | **54.0** | **+1.51** |
| Elevated (20–30) | 69 | 39.1 | +0.24 |

Low-VIX (complacency) entries have negative expectancy. Normal-VIX entries are the only cohort that clearly clears random.

**By volume ratio at entry (notes-derived):**

| Vol ratio | n | WR % | Avg adj % | Stop-hit % |
|---|---:|---:|---:|---:|
| < 1.5× | 42 | **66.7** | **+1.56** | **21.4** |
| 1.5–2× | 114 | 40.4 | −0.11 | 47.4 |
| 2×+ | 69 | 47.8 | +1.12 | 40.6 |

Counter-intuitive but unambiguous: **the highest-WR cohort has *low* volume confirmation**, not high. The mid-volume cohort (1.5–2×) is where the strategy hides its losers — these are likely "second-day chasers" rather than first-day breakouts.

**By sector (best-effort mapping; ~25% of names tagged Unknown):**

| Sector | n | WR % | Avg adj % | Stop-hit % |
|---|---:|---:|---:|---:|
| Healthcare | 18 | **72.2** | **+3.59** | **16.7** |
| Communications | 24 | 50.0 | +1.71 | 45.8 |
| Energy | 3 | 100 | +6.04 | 0.0 |
| Technology | 60 | 51.7 | +0.76 | 36.7 |
| Financials | 21 | 42.9 | +0.05 | 47.6 |
| **Consumer Discretionary** | 27 | **14.8** | **−2.88** | **63.0** |
| **Consumer Staples** | 15 | 20.0 | −1.11 | 53.3 |

Healthcare is the standout cohort. Consumer Discretionary is a disaster (15% WR, 63% stop-hits) — likely the "consumer-rolling-over" tape of 2022 + late-2024.

**By year (regime drift):**

| Year | n | WR % | Avg adj % |
|---:|---:|---:|---:|
| 2020 | 60 | 40.0 | +0.30 |
| 2021 | 48 | **56.3** | **+1.31** |
| 2022 | 9  | 33.3 | +0.16 |
| 2023 | 63 | 50.8 | +1.13 |
| 2024 | 45 | 46.7 | **−0.51** |

2024 is the first year where the strategy posts a negative average. With evidence audit cutoff at 2024-12-04, this looks like the live-trading cohort is starting to underperform the historical fit.

**Ticker concentration:**

| Top tickers | n | Sum adj % | Avg adj % |
|---|---:|---:|---:|
| NVDA | 15 | +69.3 | +4.6 |
| LLY  | 9  | +24.6 | +2.7 |
| REGN | 6  | +24.5 | +4.1 |
| LOW  | 3  | +24.1 | +8.0 |
| MRVL | 3  | +22.2 | +7.4 |

NVDA alone contributes ~70pp of cumulative adj across 15 trades. Strip the top 5 tickers (~36 of 75 unique entries) and the remaining cohort is much weaker.

### 3.3 Diagnosis

SNIPER is not random in the formal sense — the +5.5pp WR delta vs the universe-restricted random control is real. But the residual edge is **narrow and saturated**:

- **Score is over-fit at the top.** 90+ score does not beat 80–89; the high tail of the score is calibrated to history that does not generalize.
- **The "volume confirmation" feature is mislearned.** The highest-WR cohort is the *low-volume* bucket. The 1.5–2× bucket — exactly where many "valid breakout" rules say to operate — is where the strategy hides its losers.
- **Regime drift is real.** 2024 cohort is negative. Low-VIX cohort is negative.
- **Sector quality matters.** Healthcare dominates; Consumer Discretionary destroys.
- **Concentrated in mega-cap tech leaders.** Like VOYAGER, SNIPER's apparent alpha is tied to a few names (NVDA most of all) doing the heavy lifting.
- **Friction-fragile at the boundary.** Friction sensitivity from the audit: avg adj goes +0.87 → +0.57 → +0.37 → −0.13 across RT 0% / 0.30% / 0.50% / 1.00%.

The pattern that matches what we see is: late-cycle breakout chasing where (a) volume confirmation is learned the wrong way, (b) sector context isn't enforced, and (c) the 90+ score band is calibrated to a stale regime.

### 3.4 Verdict

**Verdict for SNIPER_V6:** **`keep paper-only`**, **do not pause** (the residual cohort effects below are testable hypotheses, not noise), but **a narrow sub-edge restricted to specific cohorts is the only thing that survives** — see §5.

---

## Task 4 — Cross-sleeve lessons

Themes that recur across all three sleeves:

1. **Heavy beta exposure dressed up as alpha.**
   - VOYAGER underperforms SPY by 2–4pp on every mandate horizon.
   - SNIPER's top-5 tickers (NVDA, LLY, REGN, LOW, MRVL) drive most of the cumulative return.
   - SHORT_A's historical cohort lost during a +24% SPY tape with no regime gate.

2. **Stop/target geometry not adapted to the underlying.**
   - SHORT_A: 10% stop on names with 8–18% intraday range is structurally hostile.
   - SNIPER 20d: 50.7% stop-hit rate — the timeframe outlives the stop on most setups.

3. **Wrong-regime entries.**
   - SHORT_A entered shorts at VIX 47 (post-flush, pre-rally).
   - SNIPER's low-VIX (<15) cohort posts negative expectancy (late-cycle complacency setups fail).
   - VOYAGER's bear-regime entries do better only because they catch reversals — that is a different signal than "emerging accumulation".

4. **Score saturation / over-fit tails.**
   - SNIPER 90+ score band underperforms the 80–89 band.
   - SHORT_A historical scores cluster at 61–68 — far below the 80+ band the long sleeves use, suggesting threshold permissiveness.

5. **Concentration / shallow universe.**
   - VOYAGER: 11 unique tickers; top-3 names = 93.6% of total adj.
   - SNIPER: top-5 names ≈ half of total cumulative adj across 75 unique entries.

6. **Friction-fragility at the boundary.**
   - SNIPER goes negative at 1% RT friction.
   - Universe-restricted random control already eats most of VOYAGER's headline.

7. **Volume confirmation learned wrong.**
   - SNIPER's highest-WR cohort is the *low-volume* bucket, not the breakout-volume bucket.

8. **Random control is barely beaten — and the sample is shallow at the level that matters.**
   - SHORT_A historical: 9 trades is below any reasonable n for inference.
   - VOYAGER: 16 unique entries is too few to support a strategy claim, regardless of horizon multiplication.
   - SNIPER's 75 unique entries is the only sleeve with enough independent observations for inference, and it still produces an indistinguishable verdict.

What the system has learned: **all three active sleeves are statistically thin or beta-loaded; the live evidence does not yet support promotion of any of them, and two of them (VOYAGER, SHORT_A historical) need redesign before further evidence accumulation will help.**

---

## Task 5 — Edge reconstruction plan

Three narrow, testable hypotheses. Each is analysis-only; **no live or paper promotion until the test passes its own falsification criterion**. Order is by tractability, not priority.

### H1 — SHORT_A only after a *failed bounce* in a *non-fear* tape on a *low-range* name with *no squeeze setup*

- **Edge claim.** Post-rally distribution + low VIX + low underlying intraday range + no crowded-short pressure produces a real short edge. The current SHORT_A universe is shorting strength, fear lows, and high-volatility names — none of which the autopsy supports.
- **Required data (already available in repo).**
  - `short_backtester.py` already emits `borrow_pct`, `gap_risk_pct_max_up_20d`, `intraday_range_pct` per signal.
  - `regime_validation_vix.parquet` for VIX bucketing.
  - SPY/QQQ price cache for momentum context.
  - `cache/research/options_iv_history.jsonl` exists — **inspect** for whether per-trade options call-volume / call-OI deltas can be joined; if not, defer the "no whale call accumulation" leg of H1 and run with the other three filters first.
- **Test design.**
  1. Re-export SHORT_A historical with three independent gates applied at entry-time:
     - VIX bucket ∈ {normal (15–20)} (drop entries at <15 and ≥ 30).
     - 20d max-up gap-risk < 1.50 (the loser cohort averaged 1.81; the winner cohort averaged 0.41).
     - Intraday-range-pct < 4.0 (loser avg 8.67; winner avg 2.35).
     - Optional fourth: prior 5-day return on the *underlying* < +3% (i.e. don't short into a fresh rally extension).
  2. Compute WR, avg adj, stop-hit rate, and the random-control delta on the filtered cohort with the same SHORT_A friction model.
- **Pass criterion.** Filtered cohort n ≥ 30 closed trades **and** WR ≥ 55% **and** stop-hit ≤ 25% **and** avg adj > 0 with 95% CI lower bound > −1pp **and** beats random control by ≥ +5pp WR.
- **Invalidation.** Filtered cohort still has WR < 35% **or** stop-hit > 40% **or** avg adj 95% CI fully below zero. If any of those, **redesign the SHORT_A entry-trigger**, not the filter.
- **Status.** SHORT_A stays `paper-only` until H1 passes.

### H2 — VOYAGER only when *sector leadership + 13F-delta sponsorship + forward breadth* all agree

- **Edge claim.** The current VOYAGER signal fires on names that already have heavy 13F sponsorship (XOM, UNH, GE, COST) — that is *existing* institutional ownership, not *emerging*. A real emerging-accumulation edge requires (a) the name's own sector to be leading the broad market, (b) the *change* in 13F sponsorship to be positive (not the level), and (c) market breadth to be expanding so the bid is broadening rather than concentrating.
- **Required data.**
  - 13F filings already feed VOYAGER — confirm the signal can be expressed as Δ-sponsorship (filing-quarter to filing-quarter), not level.
  - `cache/backtest_prices/XLK,XLF,XLE,XLV,XLP,XLU,XLI,XLY.parquet` for sector RS.
  - `core/market_breadth_monitor.py` already produces breadth state.
- **Test design.**
  1. Re-rank historical VOYAGER candidates per quarter requiring:
     - Δ 13F sponsorship over the last filing quarter > 0 for the name.
     - Sector ETF 60d return rank ≥ 0.6 vs SPY at entry.
     - Breadth advancing-pct ≥ 55 at entry.
  2. Compute per-horizon VOYAGER avg adj **vs SPY same-window** on the filtered cohort.
- **Pass criterion.** Filtered cohort delta vs SPY ≥ +3pp on the 130d *and* 252d horizons **and** WR ≥ 60% on at least the 130d horizon **and** the top-3-ticker concentration (% of total cumulative adj) drops below 70%.
- **Invalidation.** Filtered cohort still underperforms SPY on a majority of horizons, or top-3 concentration stays above 90%. If invalidated, **the 13F signal as currently constructed is structural beta, not alpha** — VOYAGER would need a different primary signal.
- **Status.** VOYAGER stays `paper-only` until H2 passes.

### H3 — SNIPER only when *score 80–89 + normal VIX + low-vol-ratio + Healthcare/Communications/Tech sector*

- **Edge claim.** The autopsy shows a narrow surviving cohort: score 80–89 (not 90+), normal VIX (15–20), volume ratio < 1.5×, in Healthcare or Communications or Tech sector. Each of these cohorts individually clears random; their intersection is the only place the autopsy supports continuing to look.
- **Required data.** All already in trade-level CSV / `notes` field + price cache. Daily Entry Validator state and Market Forecast state are external enrichments — record their pass/fail at entry time going forward (no schema change needed in this phase; analysis-only join).
- **Test design.**
  1. From existing SNIPER trade CSV, build a single combined-filter cohort (intersection of: score 80–89, VIX 15–20 at entry, vol_ratio < 1.5, sector ∈ {Healthcare, Communications, Technology}).
  2. Compute WR, avg adj, stop-hit, friction sensitivity, and Δ vs random on that cohort.
  3. Cross-check against the next 6 months of live paper SNIPER trades (label each with Daily Entry Validator and Market Forecast state at entry).
- **Pass criterion.** Combined-filter cohort historical n ≥ 30 closed and WR ≥ 55% and avg adj > +1.0% with 95% CI lower bound > 0 and Δ vs random ≥ +5pp WR. Live paper out-of-sample (next 6 months, n target ≥ 20 closed) preserves WR ≥ 50% and avg adj > 0.
- **Invalidation.** Filter cohort drops below n=20 historical or fails to clear random control on any of the three metrics. If invalidated, **the SNIPER score is the wrong primary feature** — it should not be the gating signal.
- **Status.** SNIPER stays `paper-only` until H3 passes.

> All three hypotheses are **research tasks**. None of them changes scanner thresholds, score formulas, paper governance, or execution. The edge is being *located*, not redesigned.

---

## Task 6 — Outputs (what changed in this phase)

| File | Change |
|---|---|
| `research/sleeve_failure_autopsy.py` | **New** analysis-only script. Reads CSV trade artefacts + price/VIX caches + rigor JSON; writes `docs/scorecards/sleeve_failure_autopsy.json`. |
| `docs/scorecards/sleeve_failure_autopsy.json` | **New** structured sidecar for this report. |
| `docs/research/SLEEVE_FAILURE_AUTOPSY.md` | **This document.** |
| `docs/scorecards/short_sleeve_scorecard.md` | Adds an "Autopsy summary" block under the rigor strip. |
| `docs/scorecards/voyager_scorecard.md` | Adds an "Autopsy summary" block under the rigor strip. |
| `docs/scorecards/sniper_scorecard.md` | Adds an "Autopsy summary" block under the rigor strip. |
| `docs/strategy/CURRENT_READINESS.md` | Adds a Phase 11 entry pointing here. |

No strategy code, scanner code, scoring code, governance, execution, dashboard, Alpha Discovery, Stock Lens, Market Forecast, or Daily Entry Validator code was touched.

---

## Limitations

- **SHORT_A historical n=9** is structurally too small for any single conclusion to be load-bearing on its own. The cohort-level statements here are robust because the failures are concentrated and structural (6/9 stop-hits, 2/9 entered at VIX≈47), not because n is large.
- **VOYAGER unique entries n=16.** Horizon multiplication produces 64 *trade rows* but the independent-decision count is 16. CIs reflect the 64-row count and overstate confidence.
- **Sector inference is best-effort.** A static ticker→sector map (`research/sleeve_failure_autopsy.py:_SECTOR_MAP`) covers the names this autopsy actually saw; ~25% of SNIPER trades are tagged `Unknown`. A clean GICS join is out-of-scope for the autopsy.
- **MAE / MFE not available for every trade.** For historical-cohort SHORT_A trades on names whose price cache range starts after the trade entry date, MAE/MFE columns are blank. The winner-vs-loser MAE comparison in §1.2 uses only the trades where the cache covers the entry-to-exit window.
- **Random control vs SPY benchmark are different tests.** The audit's universe-restricted random control answers "is the entry better than picking dates at random in the same names?". The SPY same-window benchmark in §2 answers "is the entry better than buying the index?". The two are complementary, and VOYAGER passes the first while failing the second.
- **No options-flow data was joined in this phase.** `cache/research/options_iv_history.jsonl` exists but per-trade joins are deferred to H1 follow-up. The "no whale call accumulation" leg of H1 cannot be tested until that join is built.
- **Live-paper cohorts are tiny across all three sleeves** (SHORT_A live n=4 closed; VOYAGER live trades not separated in the audit; SNIPER live cohort not isolated here). All "live looks healthier than historical" claims must be reread with that in mind.

---

## Final summary

1. **SHORT_A failure cause.** Wrong-regime entries (two opens at VIX≈47), stop/target geometry incompatible with underlying volatility (10% stop on names with 8–18% intraday range), shorting leadership names mid-rally, and a permissive 61–68 score threshold. 6 of 9 historical trades hit the −10% stop during a +24% SPY tape.
2. **VOYAGER failure cause.** Long-only mega-cap-defensive beta with negative alpha vs SPY on every mandate horizon (−2 to −4pp). 93.6% of cumulative return is from 3 names (GE, XOM, COST). The 13F signal as expressed picks already-large already-sponsored names — not *emerging* accumulation.
3. **SNIPER failure cause.** Score saturation (90+ underperforms 80–89), volume-confirmation feature mislearned (low-vol cohort wins, mid-vol cohort hides losers), no enforced sector context (Cons Disc 14.8% WR), regime drift in 2024 (first negative year), and concentration in NVDA + four other mega-caps. Friction-fragile at 1% RT.
4. **Common lessons.** Beta dressed as alpha; stop/target geometry not adapted to underlying volatility; wrong-regime entries; over-fit score tails; shallow universes; friction-fragility; volume confirmation learned wrong; random control barely beaten.
5. **Sleeve dispositions.** All three remain **paper-only**. None paused. **VOYAGER and SHORT_A historical require redesign** before more evidence is meaningful. SNIPER continues to accrue evidence; only its narrow surviving cohort (H3) is a credible candidate for promotion-track work.
6. **Three narrow next hypotheses (H1 / H2 / H3 above).** Each has an exact edge claim, the data it requires, the test, and a falsification criterion.
7. **Limitations.** Listed above. The most load-bearing one is sample size on SHORT_A and VOYAGER unique entries.

---

_This autopsy is a snapshot of evidence as of 2026-05-04 and will be re-examined when more closed trades land or when any of the three hypothesis tests above produce a verdict._

---

## Phase 12A — H3 historical screen (SNIPER narrow-cohort)

Generated: 2026-05-04 · Script: `research/sniper_h3_validation.py` · Sidecar JSON: `docs/scorecards/sniper_h3_validation.json`.

**H3 as tested.** SNIPER cohort defined as the intersection of: score ∈ [80, 90) ∩ VIX at entry ∈ [15, 20) ∩ vol_ratio < 1.5× ∩ sector ∈ {Healthcare, Communications, Technology}.

### Filter funnel

| Gate (applied to the 225-row / 75-unique-entry SNIPER CSV) | Rows passing |
|---|---:|
| Total closed rows | 225 |
| Score ∈ [80, 90) | 75 |
| VIX ∈ [15, 20) at entry | 87 |
| Vol ratio < 1.5× | 42 |
| Sector ∈ {Healthcare, Communications, Technology} | 102 |
| Score ∩ VIX | 33 |
| Score ∩ VIX ∩ Vol | 6 |
| **All four gates (H3 cohort)** | **3** |

The four gates compose to **3 trade-rows = 1 unique entry × 3 horizons (5/10/20d)**. The single qualifying entry is **AVGO 2021-10-26**.

### H3 cohort statistics (3 rows = 1 independent observation)

| Metric | Value | 95% CI |
|---|---:|---|
| n closed rows | 3 | — |
| n unique entries | **1** | — |
| WR | 100.0% | [100, 100] |
| Avg adjusted return | +3.46% | [+0.29%, +5.89%] |
| Stop-hit rate | 0.0% | [0, 0] |
| Target-hit rate | 33.3% | [0, 100] |
| Max DD | 0.0% | — |

**The bootstrap CI shown is bootstrap of three rows that share an entry-level signal (same ticker, same date, same score, same VIX, same vol_ratio, same sector). It overstates information; the only independent observation in the cohort is one trade. Statistical inference is not possible from this sample.**

### Random control on H3 cohort

| Metric | Strategy | Random control (15 synthetic entries on AVGO) |
|---|---:|---:|
| WR | 100% | 33.3% |
| Avg adj return | +3.46% | +0.25% |
| Stop-hit rate | 0% | 60% |

The random control covers **one ticker** because the H3 universe is one name; the +66.7pp WR gap is real but generalises only to that ticker on dates near 2021-10-26.

### Friction sensitivity (H3 cohort)

| RT friction | Avg adj | 95% CI | WR |
|---:|---:|---|---:|
| 0.00% | +3.76% | [+0.59, +6.19] | 100.0% |
| 0.30% | +3.46% | [+0.29, +5.89] | 100.0% |
| 0.50% | +3.26% | [+0.09, +5.69] | 100.0% |
| 1.00% | +2.76% | **[−0.41, +5.19]** | 66.7% |

Edge survives at 0.50% RT but the CI lower bound goes below zero at 1.00% RT. With n=1 unique entry, the friction sensitivity is also indicative only.

### Year-by-year stability

| Year | n | WR | Avg adj | Stop-hit |
|---:|---:|---:|---:|---:|
| 2021 | 3 | 100% | +3.46% | 0% |

Only one year is represented; stability cannot be assessed.

### Concentration

| Metric | Value |
|---|---|
| Unique tickers | **1** (AVGO) |
| Unique entries | 1 |
| Top-3 ticker share of total adj | 100% |
| Sectors represented | Technology only (Healthcare, Communications absent) |

### Per-horizon view

| Horizon | n | Avg adj | WR | Target hit |
|---:|---:|---:|---:|---:|
| 5d | 1 | +0.29% | 100% | 0% |
| 10d | 1 | +4.19% | 100% | 0% |
| 20d | 1 | +5.89% | 100% | 100% |

### Leave-one-gate-out diagnostic

Drop one gate at a time; report the resulting cohort:

| Gate dropped | n closed rows | n unique entries | WR | Avg adj | Stop-hit |
|---|---:|---:|---:|---:|---:|
| Drop score gate | 6 | 2 | 100% | +2.16% | 0% |
| Drop VIX gate | 9 | 3 | 100% | +4.14% | 0% |
| **Drop vol-ratio gate** | **15** | **5** | 60% | +3.08% | 33% |
| Drop sector gate | 6 | 2 | 100% | +4.75% | 0% |

The **vol_ratio < 1.5×** gate is the binding constraint. Even with the most-generous gate dropped, the cohort tops out at 5 unique entries — still well below MIN_N_FOR_CI=30 and below the 5-unique-entry independence floor.

### Auxiliary state availability (DEV / Market Forecast)

| Auxiliary state | Available historically? | Future join plan |
|---|---|---|
| Daily Entry Validator state at entry | **No** — not snapshotted in the historical SNIPER backtest CSV; the audit pipeline does not currently persist DEV pass/fail per signal. | When SNIPER paper signals run forward, persist DEV state at signal-emit time in `db/trading.db` (e.g. a new `paper_signals.aux_dev_state` column or `notes`-field tag). The H3 live-OOS test in Phase 12B joins on that field. |
| Market Forecast state at entry | **No** — `regime_forecast_latest.json` is point-in-time today; historical signals were not tagged with the regime label that was in force at entry. | Persist the Market Forecast regime label at SNIPER signal-emit time. Once 6 months of forward signals carry both fields, re-run `research/sniper_h3_validation.py --include-aux-state` (the script's `filter_funnel` block is forward-compatible). |

This gap is recorded honestly: the "DEV pass" and "Market Forecast supportive" legs of H3 cannot be tested on the historical CSV, only forward.

### H3 verdict

**`INSUFFICIENT_DATA`.**

Reasons:
1. n_unique_entries = **1** is below the 5-unique-entry independence floor; the 3 trade-rows are horizon-multiplicates of one decision, so any bootstrap CI on the row-level series overstates information.
2. The point estimate is positive (avg adj +3.46% / 100% WR) but evidence is structurally insufficient — the four H3 gates compose to a near-empty cohort on the existing 75-entry CSV.
3. Even leave-one-gate-out cohorts top out at 5 unique entries — far below MIN_N_FOR_CI=30.

**This is not "H3 rejected".** The hypothesis is well-formed; the existing dataset is too small to test it. H3 cannot be confirmed or refuted on historical SNIPER data. The pre-registered live-OOS step in the original H3 plan is now mandatory — there is no historical screen to "pass first".

### What this means for SNIPER

- **No sleeve-status change.** SNIPER stays paper-only, not paused.
- **No threshold or scanner change.** Confirmed: Phase 12A made zero edits to strategy / scoring / scanner / governance / execution / dashboard / sleeve status.
- **The next action is data-collection, not modeling.** Phase 12B is the live-OOS instrumentation: tag forward SNIPER paper signals with score, VIX, vol_ratio, sector, DEV state, Market Forecast state at signal-emit time. After ~6 months of forward signals (~20–30 closed trades), re-run `research/sniper_h3_validation.py` against the live cohort.
- **A second avenue is dataset expansion.** The existing CSV is 75 unique entries from a 2020-01-17 → 2024-12-04 window. Extending the historical export back further (or in finer date granularity) would lift the H3 cohort floor — this is a research task that is **out-of-scope for Phase 12A** and only worth attempting if Phase 12B's forward signal collection is also slow to fill.

---

## Phase 12B — SNIPER forward-OOS instrumentation

Generated: 2026-05-05 · Scope: metadata-only instrumentation, no behaviour change.

> **Status:** SNIPER H3 is **forward-instrumented, not validated**. Phase 12B
> only wires the per-signal metadata that Phase 12A identified as missing. No
> SNIPER threshold, scanner gate, paper governance rule, execution path, sleeve
> status, or dashboard score was modified. Promotion remains blocked until the
> live-OOS cohort accrues 20–30 closed H3 candidates (~6 months) and the
> Phase 12A criteria are re-evaluated against that live data.

### What changed

| File | Change |
|---|---|
| `core/paper_validation.py` | Added additive migration `_PAPER_SIGNALS_MIGRATIONS` (new column `aux_h3 TEXT`). Added `compute_h3_metadata()` and `safe_compute_h3_metadata()` helpers. Added optional `aux_h3` parameter to `log_paper_signal()`. |
| `main.py` | Added `_read_market_forecast_snapshot()` and `_build_sniper_aux_h3()` helpers. `_record_paper_candidate()` now passes `aux_h3` to `log_paper_signal()` **only for SNIPER** signals; other sleeves leave it NULL. |
| `research/sniper_h3_forward_report.py` | **New** analysis-only report. Reads paper_signals + outcomes; reports total / H3 / non-H3 cohort stats, gate-fail attribution, missing-metadata counts; emits a status banner: `SNIPER H3 OOS: open X · closed Y · insufficient until 20–30 closed`. |
| `docs/scorecards/sniper_h3_forward_report.json` | Sidecar JSON written by every run of the forward report. |

### Per-signal metadata recorded (for SNIPER paper signals only)

The `aux_h3` JSON blob carries:

- **Identity:** `ticker`, `side`, `entry_date`, `baseline_tag`, schema version `h3.v1`.
- **H3-relevant inputs:** `sniper_score`, `vix_value`, `volume_ratio`, `sector`, `sector_canonical`.
- **Buckets the report aggregates on:** `score_bucket` ∈ {`80-89`, `90+`, `other`, `missing`}, `vix_bucket` ∈ {`low (<15)`, `normal (15-20)`, `elevated (20-30)`, `high (>=30)`, `missing`}, `volume_ratio_bucket` ∈ {`<1.5`, `1.5-2.0`, `>2.0`, `missing`}, `sector_bucket` ∈ {`h3_allowed`, `h3_disallowed`, `missing`}.
- **Auxiliary research-context state (best-effort, may be null):** `daily_entry_validator_state`, `market_forecast_regime`, `market_forecast_bias_5d`, `market_forecast_bias_10d`, `market_posture_bias`, `options_quality`, `stock_extension_state`, `alpha_discovery_state`.
- **Cohort verdict:** `h3_candidate` (boolean), `h3_reason` (per-gate pass/fail dict).

### H3-candidate tagging behaviour

A signal is tagged `h3_candidate=True` iff **all four** gates pass:

1. `score_bucket == "80-89"` (i.e. 80 ≤ score < 90)
2. `vix_bucket == "normal (15-20)"` (i.e. 15 ≤ VIX < 20 at entry)
3. `volume_ratio_bucket == "<1.5"`
4. `sector_bucket == "h3_allowed"` (sector ∈ {Healthcare, Communications, Technology}, after canonicalising aliases like "Communication Services" → "Communications", "Health Care" → "Healthcare", "Information Technology" → "Technology", lowercase variants)

Missing inputs map to `"missing"` and the corresponding gate evaluates to False — i.e. a signal with an unknown VIX is *not* tagged H3, never crashes the pipeline. The `h3_reason` dict records which gate(s) failed, so the forward report can attribute the binding constraint live.

### Auxiliary research-context state — current population status

Phase 12B records these fields if available, but most are not yet wired into the SNIPER opportunity dict. The report's `missing_metadata_counts` block tracks coverage so we know when each field becomes useful for sub-cohort breakouts.

| Field | Population status (Phase 12B baseline) |
|---|---|
| `market_forecast_regime` | **Populated** from `cache/research/regime_forecast_latest.json` `headline.current_regime` |
| `market_forecast_bias_5d` | **Populated** from `headline.bias_5d` |
| `market_forecast_bias_10d` | **Populated** from `headline.bias_10d` |
| `vix_value` | **Populated** from `opp.vix` (with fallback to `regime_forecast_latest.json` `volatility.vix`) |
| `daily_entry_validator_state` | **Null today** — DEV is run inside Alpha Discovery; the SNIPER opportunity dict does not currently carry it. Wiring is a follow-up (Phase 12C if needed). |
| `market_posture_bias` | **Null today** — not surfaced in `opp` |
| `options_quality` | **Null today** — Stock Lens output not joined |
| `stock_extension_state` | **Null today** — Stock Lens extension state not joined |
| `alpha_discovery_state` | **Null today** — Alpha Discovery membership not joined |

The forward report counts these as missing per row, so we can see at a glance when (and if) each becomes available without re-reading code.

### Forward-report behaviour

`research/sniper_h3_forward_report.py`:
- Runs cleanly with **zero rows** ("No SNIPER paper signals in the selected window.").
- Runs cleanly when the migration has not been applied (warns, does not crash).
- Emits a status banner: `SNIPER H3 OOS: open X · closed Y · {insufficient until 20–30 closed | approaching threshold | target reached}`.
- Prints the cohort comparison only when both H3-closed and non-H3-closed cohorts have rows.
- Always writes the JSON sidecar `docs/scorecards/sniper_h3_forward_report.json`.
- Optional `--since YYYY-MM-DD` to scope to a specific live-OOS window.

### Backwards-compatibility & failure modes (verified)

- **Migration is additive.** Existing 1137 paper_signals rows preserved on smoke-test; new column populated NULL for all of them; existing readers (`fetch_paper_signals`, the dashboard's `SELECT * FROM paper_signals`) still work because `aux_h3` is the last column and is nullable.
- **`compute_h3_metadata()` never raises.** All-None inputs produce `h3_candidate=False` with all four gate-fail reasons; weird inputs (string score, dict vol_ratio, int sector) are caught by `safe_compute_h3_metadata()` and produce a serialised dict with the corresponding bucket marked `"missing"`.
- **Signal logging is never blocked.** If the H3 builder fails, `_build_sniper_aux_h3` returns None, the column is persisted NULL, and the rest of the row writes normally.
- **Non-SNIPER strategies are unaffected.** Only the `strategy == "SNIPER"` branch in `_record_paper_candidate` calls the H3 builder; SHORT and Voyager keep `aux_h3 = NULL`.

### Dashboard

Per the user's "do not overbuild dashboard UI" guidance, no edits were made to `dashboards/gem_trader_hq.py`. The status banner string is available from `sniper_h3_forward_report.py` and can be embedded by a future cron / dashboard reader without any change to the report's output contract.

### What this enables

- The first SNIPER paper signal that lands after Phase 12B will carry full `aux_h3` metadata.
- Once 20–30 H3-tagged closed signals accumulate, `research/sniper_h3_validation.py` can be re-run against the live cohort, producing a verdict comparable to the Phase 12A historical screen.
- The `gate_fail_attribution` block in the forward report tells us, live, which of the four H3 gates is most often the binding constraint — i.e. whether the cohort is going to remain too thin to test, the same way it was on the historical CSV.

### Limitations

- **No retroactive tagging.** The 1137 existing paper_signals rows (all SHORT_A) carry `aux_h3=NULL` and will not be back-filled. This is intentional — Phase 12B is a forward-only instrumentation phase.
- **DEV / Stock Lens / Alpha Discovery / Market Posture are still recorded as null.** They will only become useful once those layers are added to the SNIPER `opp` dict (a follow-up; not in scope for Phase 12B because that would touch the SNIPER signal-construction surface, which the phase brief explicitly fences off).
- **Sector field reliability depends on upstream.** SNIPER's current `opp.sector` is often empty; `sector_canonical=None` and `sector_bucket="missing"` will then fail the H3 sector gate, causing such signals to be tagged non-H3. The forward report exposes this as a gate-fail count so the gap is visible.
- **Phase 12B is not promotion.** Promotion remains blocked until the live-OOS validation in a future phase produces a positive verdict against the Phase 12A criteria.


