# LEADER_RESET — Validation Plan (backtest → forward-test → paper gate)

**Date:** 2026-05-24
**Status:** Plan only. No backtest is run and no sleeve is activated by this document.
**Companion docs:** `LEADER_RESET_V0_SPEC.md` (the design), `STRATEGY_TRUTH_REVIEW_2026_05.md` (the motivation).
**Doctrine:** Promotion ladder is non-negotiable — research → backtest → paper → shadow → limited live → full live. LEADER_RESET does not become a paper sleeve until the backtest gates below pass. It does not approach live capital before the holdout closes (2026-12-01) and the full ladder is satisfied.

---

## 1. What we are trying to prove (and disprove)

**Hypothesis (H1):** Among confirmed liquid leaders, entries taken *on a reclaim after a controlled, volume-contracted pullback* produce positive risk-adjusted forward returns (1–10d) materially better than (a) a random-entry baseline in the same names and (b) entries taken while the name is *extended*.

**Null (H0):** Reclaim-timed entries are indistinguishable from random entries in leaders — i.e. the "edge" is just leadership beta, not timing.

The plan is designed to **try to kill H1**, not to confirm it. If H1 survives the controls below, the timing overlay is real.

---

## 2. Historical data needed

| Data | Source | Granularity | Use |
|---|---|---|---|
| Daily OHLCV (universe + SPY/QQQ/IWM + sector ETFs) | Alpaca SIP, `cache/prices/*.parquet` | daily, ≥ 2 yrs + ideally a drawdown regime (e.g. include 2022-style stress if available) | EMA20/50/200, VWAP-proxy, ATR(14), RS, extension, volume contraction, forward returns |
| Point-in-time leadership inputs | Reconstructed from OHLCV + as-of fundamentals | daily | Recompute RS / extension / pullback **as of each historical date** — do NOT use today's Alpha Discovery board (lookahead). |
| Sector rotation state | Sector ETF parquets | daily | Sector improving/leading filter, point-in-time |
| VIX history | `cache/research/regime_forecast_vix_history.json` | daily | Regime gate reconstruction |
| Regime classification | Recompute via `REGIME_FORECASTER_V1` walk-forward, or use `regime_forecast_walkforward_rows.csv` | daily | Regime gate, point-in-time |
| Earnings dates | FMP earnings calendar (cached) | per event | Earnings-proximity exclusion, point-in-time |
| Friction model | `ROUND_TRIP_FRICTION_PCT` (15 bps one-way) | constant | Net-of-cost returns |

**Critical:** the live spec reads the *current* Alpha Discovery board. For backtest we **cannot** use that artifact (it is computed with present data). All leadership/extension/entry-state features must be **recomputed point-in-time from raw OHLCV and as-of fundamentals.** The backtest validates the *definition* in the spec, not the cached artifact.

---

## 3. Avoiding lookahead bias

- **Point-in-time everything.** On simulated date `T`, only data with timestamp `≤ T` may inform the setup/trigger. MAs, ATR, RS, extension, volume contraction, regime, sector state, earnings calendar — all as-of `T`.
- **Reclaim trigger uses bar `T` close; entry fills at `T+1` open** (no same-bar fill at the close that defined the signal). Forward returns measured from the `T+1` open.
- **Stops/targets evaluated intrabar** using `T+1..T+N` highs/lows; ambiguous same-bar stop-and-target resolved pessimistically (assume stop first).
- **No survivorship bias:** universe membership must be as-of `T` (include names later delisted). If the parquet cache is survivorship-biased, document it as a known limitation and discount win-rate accordingly.
- **As-of fundamentals only**: sponsorship/fundamental fields must use the vintage available at `T`, not restated values.
- **Frozen parameters before the run.** All thresholds (price floor, $25M ADV, ±3% EMA20, +8% prior extension, 3–12% pullback depth, 0.8× contraction, 1.2× reclaim volume, 1.5×ATR stop, 0.5% risk) are fixed in `LEADER_RESET_V0_SPEC.md` **before** the backtest. No post-hoc tuning on the test set (see §8 train/test split).

---

## 4. Event construction rules

1. **Scan each (ticker, date) in-universe** for a valid setup per spec §4 (leadership + controlled pullback + quality), all point-in-time.
2. **A "reset event" = first bar a valid setup also meets the reclaim trigger** (spec §5). One event per pullback episode — once triggered, the same episode cannot re-trigger until the name exits the setup and forms a new pullback (mirrors the live dedup/cooldown).
3. **Per-ticker cooldown** of 5 trading days after an event before a new event in the same name.
4. **Record the label** at `T` for *every* in-universe candidate (`RESEARCH_READY` / `WATCH_RECLAIM` / `LATE_EXTENDED` / `BLOCKED` / `NO_EDGE`), so we can measure each cohort's forward behavior, not just the triggered ones.
5. **Apply risk feasibility** (stop, size, heat-cap, friction) at `T`; events failing it are reclassified `BLOCKED` and excluded from the tradable cohort (but kept for cohort analysis).

---

## 5. Control groups

To separate *timing edge* from *leadership beta*:

- **C1 — Random-entry-in-leaders baseline.** For every `RESEARCH_READY` event, draw N random entry dates in the *same ticker* within ±15 sessions while it was in-universe-leader. If reclaim timing has no edge, the `RESEARCH_READY` cohort ≈ C1.
- **C2 — Extended-entry cohort.** Entries taken when the same leaders are `LATE_EXTENDED` (extended, not reset). Thesis predicts `RESEARCH_READY` > C2.
- **C3 — Pure market baseline.** SPY/QQQ buy-and-hold over identical forward windows (is the sleeve beating just being long the index?).
- **C4 — Watch-no-trigger cohort.** `WATCH_RECLAIM` names that never triggered — did skipping them cost or save return? (Validates that the *trigger*, not just the setup, adds value.)

Each cohort is evaluated on identical forward windows and identical friction.

---

## 6. Forward hold periods

Measure every event at **1d, 3d, 5d, 10d** (matching `paper_signal_outcomes` horizons and the active-sleeve tactical horizons). Report both **raw** and **friction-adjusted** (15 bps one-way) returns, and a **stop/target-path** version (apply spec §7 exits) alongside the fixed-horizon version.

---

## 7. Metrics

Per cohort, per horizon:

- **Expectancy** (avg R per event) — primary.
- **Win rate** and **payoff ratio** (avg win / avg loss).
- **Average win / average loss** (in % and in R).
- **MAE** (max adverse excursion) and **MFE** (max favorable excursion) distributions — especially the MAE tail (does the 1.5×ATR stop hold?).
- **Max drawdown** of the event-equity curve.
- **Turnover / event frequency** (events per month — must be enough to accumulate evidence in 30–60d, but not SHORT_A-style spam).
- **Hit-rate vs C1/C2/C3/C4** with a significance check (bootstrap CI on expectancy difference; flag if n too small to conclude).
- **Slippage sensitivity**: re-run net returns at 10 / 15 / 25 bps one-way to confirm the edge survives realistic friction.
- **Regime-conditional breakdown**: expectancy by regime (Bull Continuation / Pullback / Chop) to confirm the regime gate is doing real work.

---

## 8. Train/test discipline

- **Split:** parameters frozen on an in-sample period (e.g. older 60–70% of history); all headline gate metrics reported on a **held-out out-of-sample** tail the parameters never saw.
- **Walk-forward** preferred over a single split where data depth allows (reuse the regime walk-forward harness pattern).
- **One shot on the test set.** If the OOS result fails, we redesign the *thesis*, not re-grid the parameters on the test data.

---

## 9. Pass / fail gates (before LEADER_RESET becomes a paper sleeve)

All must pass on **out-of-sample**, friction-adjusted, with adequate sample (≥ ~40 events; if fewer, gate = "insufficient evidence, extend history"):

| # | Gate | Threshold |
|---|---|---|
| G1 | Positive expectancy | Friction-adjusted expectancy > 0 at 5d **and** 10d. |
| G2 | Beats random-in-leaders (C1) | `RESEARCH_READY` expectancy > C1 by a bootstrap-significant margin (timing adds value, not just leadership beta). |
| G3 | Beats extended entries (C2) | `RESEARCH_READY` expectancy > C2 (reset > chase). |
| G4 | Risk shape sane | Avg loss ≤ ~1R (stop holds); MAE tail not dominated by gap-throughs; no single-name loss > ~2.5R. |
| G5 | Frequency workable | ≥ ~4 tradable events/month in constructive regimes (enough to accumulate evidence), and **not** > ~1 event/name/week (no SHORT_A-style re-emission). |
| G6 | Survives friction | Edge remains positive at 25 bps one-way. |
| G7 | Regime gate earns its keep | Expectancy in gated-out regimes is ≤ gated-in (the gate isn't discarding good trades). |
| G8 | Beats index (C3) | Risk-adjusted return ≥ SPY buy-and-hold over matched windows (sleeve adds something over being long). |

**Fail any gate → do not activate the paper sleeve.** Either redesign the thesis/trigger (new spec version) or archive the candidate. We do not relax a gate to pass.

---

## 10. From backtest pass → paper sleeve (the next ladder rung, out of scope here)

Only after G1–G8 pass:
1. Strengthen the **forward-outcome resolver** so paper signals mature on cadence (today 0 of 58 regime snapshots are matured — this must be fixed first).
2. Add LEADER_RESET to `core/strategy_registry.py` as a **paper, non-execution** sleeve with the dedup/cooldown/heat-cap pre-check from spec §6 built in.
3. Run **30–60 days of paper-only** evidence; resolve outcomes at 1/3/5/10d; re-evaluate against the same metrics live.
4. Compare paper expectancy to the backtest OOS expectancy — large divergence ⇒ stop and diagnose (overfit or implementation drift) before any further ladder step.

No shadow/limited-live consideration until paper evidence is clean *and* the holdout closes (2026-12-01) *and* the full promotion ladder is satisfied. No live trading, execution, governance, or registry change is authorized by this plan.
