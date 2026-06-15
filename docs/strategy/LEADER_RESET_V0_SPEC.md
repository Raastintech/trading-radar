# LEADER_RESET v0 — Design Spec (paper-only, NOT implemented)

**Date:** 2026-05-24
**Status:** Design only. This document is a specification, not code. **No scanner, paper signal, registry entry, governance hook, or execution path is created by this document.** LEADER_RESET is not active and is not in `core/strategy_registry.py`.
**Companion docs:** `STRATEGY_TRUTH_REVIEW_2026_05.md` (why), `LEADER_RESET_VALIDATION_PLAN.md` (how we prove it).
**Codename / proposed baseline tag:** `LEADER_RESET_V0` (research-only until validation gates pass).

---

## 1. Thesis

> **Buy institutional-strength leaders after a controlled pullback and reclaim — not during extension.**

The recurring failure documented in the truth review is that the system is flat or short while validated leaders run, and its discovery engine (Alpha Discovery) already identifies those leaders but has no sleeve to act on them with disciplined timing. LEADER_RESET closes that loop: take a name that is *already* a confirmed leader (strong multi-horizon RS, above rising 50d/200d, healthy sponsorship), wait for it to *cool* to a structural support (EMA20 / VWAP / prior swing), confirm volume contraction on the pullback, and only then — on a **reclaim trigger** — log a risk-capped paper signal.

It is explicitly a **timing and risk overlay on existing leadership discovery**, not a new selection engine. ~80% of the inputs already exist on the Alpha Discovery "Liquid Leadership Reset" track (`entry_state`, `entry_validator_state`, `options_quality`, `crowd_penalty`, `sponsorship_score`, `return_20d_pct`, `volume_ratio_5d`, `validator_state`, `why_now`/`why_not`).

**This is a research/paper sleeve. It never emits "BUY NOW." It emits labels.**

---

## 2. Universe

Candidate universe is the intersection of the standard liquid US equity universe and the Alpha Discovery leadership track.

Inclusion:
- Liquid US common stock (no ADR/OTC junk), primary listing.
- Price ≥ **$10** (avoid low-priced noise; tunable, conservative default).
- 20-day average dollar volume ≥ **$25M** (`avg_dollar_volume_20` from Alpha Discovery, or computed from `cache/prices`).
- Present on Alpha Discovery's **Liquid Leadership Reset** track, OR independently passing the leadership filter in §4 (the track is the fast path; the filter is the definition).
- Optional: adequate options liquidity for the `options_quality` confirm (not required — neutral/missing is allowed; see §4).

Exclusion:
- Extreme low-float / sub-$10 / illiquid names.
- Leveraged/inverse ETFs, ETNs.
- **Earnings within 1–2 trading days** (from cached FMP earnings calendar) — *unless* the setup is explicitly a `POST_EARNINGS_RECLAIM` variant (deferred to a later version; v0 simply excludes earnings-proximate names).
- Names flagged `bucket = "Too Late / Crowded"` with high `crowd_penalty` (these become `LATE_EXTENDED`, not tradable).

---

## 3. Regime gate (checked first, cheap, kills the whole sleeve when off)

LEADER_RESET only produces tradable labels when the market backdrop supports long leadership. All of the following must hold (read from `cache/research/regime_forecast_latest.json` and the MCP audit sidecar):

- SPY **above its 50d and 200d** MA.
- VIX **below stress threshold** (default **22**; soft caution band 18–22 reduces eligible count but does not hard-stop).
- Forecaster `current_regime` ∈ {Bull Continuation, Bull Pullback / Buy-the-Dip, Chop / Range} and **not** Risk-Off / Volatility Expansion / Stress / Bear Rally.
- Forecaster `strategy_favorability` for long-leadership not "avoid".
- MCP audit session state **not** `BLOCKED` and **not** `STALE` (from `cache/research/mcp_analysis_latest.json`).

If the regime gate fails, **every candidate is labeled `BLOCKED`** with the failing reason; no `RESEARCH_READY` is possible. This is the feature that keeps the sleeve correctly quiet in risk-off — and it is logged, so silence is auditable.

---

## 4. Setup (the leader must qualify *before* we look for a trigger)

A candidate is a valid **reset setup** only if all hold. These are point-in-time, computable from `cache/prices/{TICKER}.parquet` + Alpha Discovery fields:

**Leadership (the name must already be strong):**
- Strong relative strength: positive 20d **and** 60d RS vs SPY (proxy: `return_20d_pct` beats SPY 20d; 60d RS computed from parquet).
- Price above **rising** 50d and 200d MA.
- Sector improving or leading (sector rotation row state ∈ {Leading, Improving} from `regime_forecast.sector_rotation`), OR sponsorship_score above median.

**Controlled pullback (it must have cooled, not broken):**
- Recent extension has come *off*: distance to EMA20 has compressed from a recent high (was extended, now near EMA20). Operational: `dist_ema20` now within **±3%** after having been > **+8%** within the prior 10 sessions.
- Price near EMA20 **or** reclaiming VWAP/EMA20 (within 2% below to just above).
- **Volume contraction on the pullback**: pullback-leg average volume < **0.8×** the 20d average (`volume_ratio_5d < 1.0` and falling). A pullback on *expanding* volume is distribution, not a reset → disqualify.
- Pullback depth bounded: drawdown from recent swing high between **3% and 12%** (shallow = not a real reset; deep = trend damage).

**Quality / non-junk gates:**
- `options_quality` ∈ {confirming, neutral, missing, no_edge} is **allowed**; `options_quality = BEARISH_HEDGE` → disqualify (bearish options positioning against the reclaim).
- Not `SPECULATIVE_CALL_CHASE` (Alpha Discovery `alpha_flags`).
- Not `Too Extended` (`entry_state`/`validator_state` = "too extended" → label `LATE_EXTENDED`, not a setup).

A name passing all setup conditions but **without a trigger yet** is labeled **`WATCH_RECLAIM`** (this aligns with Alpha Discovery's existing `entry_state = "watch reclaim"`).

---

## 5. Trigger (the entry event — only fires on a fresh reclaim)

Given a valid setup, emit `RESEARCH_READY` only when **all** trigger conditions are met *on the evaluation bar*:

- **Reclaim event**: close back above **EMA20** *or* above **VWAP** *or* above the **prior day's high** (whichever the setup was tracking), having been below it on the pullback.
- **Volume confirmation**: reclaim-bar volume ≥ **1.2×** 20d average (participation on the reclaim, contrasting the contraction during the pullback).
- **Options quality** confirms or is neutral (not bearish-hedge).
- **Gatekeeper** verdict ≠ `BLOCK` (`cache/research/executive_gatekeeper_{TICKER}_latest.json`, freshness-checked per Phase 2B.2 thresholds).
- **Entry Validator** state clean (not "too extended", not blocked) — reuse the existing entry-validator artifact.

No trigger on a given bar → the name stays `WATCH_RECLAIM` (still a valid setup, awaiting the event) or downgrades to `LATE_EXTENDED` / `NO_EDGE` if the setup decays.

---

## 6. Risk (must pass *before* a paper signal is logged)

Risk is defined at the moment of the trigger and is a **hard precondition for logging** — this is the explicit fix for SHORT_A's "log first, block later" noise:

- **Stop**: the *tighter-bounding* of (a) below the recent swing low, or (b) entry − **1.5 × ATR(14)**. ATR from `cache/prices`.
- **Risk per trade capped**: default **0.5%** of paper equity at risk per position (single source; tunable in plan, not in `core/config.py`).
- **Position size** = floor( risk_budget / per-share-risk ), then **must pass the heat-cap / concentration pre-check** (reuse `portfolio_concentration_report` logic conceptually) *before* the signal is written. If size rounds to 0 or violates heat cap → **do not log**; emit `BLOCKED` with reason, not a zero-allocation paper row.
- **Spread/slippage feasibility**: if estimated spread or modeled slippage exceeds the friction budget (reuse `ROUND_TRIP_FRICTION_PCT` = 15 bps one-way from `resolve_tactical_outcomes.py`), → `NO_EDGE`/`BLOCKED`, do not log.
- **Dedup + cooldown** (anti-SHORT_A): one open paper signal per ticker; after a signal closes, a per-ticker cooldown (default **5 trading days**) before re-logging the same name. The setup-state hash (`setup_state_hash`, already a column on `paper_signals`) is used to suppress re-emission of an unchanged setup within a day.

Target structure (for outcome measurement, not a live order): first scale at **1R**, runner to **2R**.

---

## 7. Exit (paper bookkeeping rules for outcome resolution)

- Partial at **1R**, remainder trails.
- **Trailing stop**: to breakeven after 1R; thereafter trail under EMA20 or a chandelier 3×ATR, whichever is tighter.
- **Time stop**: exit after **5–10 trading days** if neither 1R nor stop is hit (default 8; matches the paper-outcome horizons 1/3/5/10).
- **Regime exit**: if the regime gate flips to fragile/stress/risk-off (VIX > 22, SPY < 50d, MCP state FRAGILE/BLOCKED), flag all open LEADER_RESET paper signals for exit-on-next-bar.

These rules feed `paper_signal_outcomes` (horizons 1/3/5/10) for forward evidence; they are bookkeeping, not live orders.

---

## 8. Output labels (the only thing the sleeve emits)

No "BUY NOW" language anywhere. Labels map onto Alpha Discovery's existing `entry_state` vocabulary so the two layers stay consistent:

| Label | Meaning | Maps to Alpha Discovery `entry_state` |
|---|---|---|
| `RESEARCH_READY` | Valid leader + controlled pullback + reclaim trigger + risk passes. Logged as a paper signal. | (reclaim fired) |
| `WATCH_RECLAIM` | Valid setup, no trigger yet. Watchlist only. | `watch reclaim` |
| `LATE_EXTENDED` | A leader, but currently extended / crowded — not a reset. No action. | `too extended` |
| `BLOCKED` | Regime gate / gatekeeper / heat-cap / earnings-proximity blocks it. Reason logged. | (n/a) |
| `NO_EDGE` | Fails leadership or pullback quality, or friction too high. | `watch only` / fails |

Only `RESEARCH_READY` writes a paper signal. `WATCH_RECLAIM` and `LATE_EXTENDED` are watchlist artifacts. `BLOCKED`/`NO_EDGE` are logged for auditability (so silence is explainable) but never written as zero-allocation paper rows.

---

## 9. What this v0 deliberately does NOT do

- No live order, no `OrderManager` coupling, no governance change, no `core/config.py` edits.
- No registry entry until validation gates pass (`core/strategy_registry.py` untouched).
- No new provider integration (reuses Alpaca/FMP/Tradier already in the stack).
- No `POST_EARNINGS_RECLAIM` variant (deferred to v1).
- No multi-position-per-name pyramiding.
- No tuning of existing sleeves to accommodate it.

---

## 10. Reuse map (why this is cheap to build)

| Need | Existing component |
|---|---|
| Leadership universe + extension + entry-state | `cache/research/alpha_discovery_board_latest.json` (Liquid Leadership Reset track) |
| OHLCV / EMA / ATR / RS / volume | `cache/prices/{TICKER}.parquet` via existing readers |
| Regime gate | `cache/research/regime_forecast_latest.json` + `cache/research/mcp_analysis_latest.json` |
| Earnings proximity | FMP earnings calendar (cached 6h) |
| Options confirm | `core/options_feed_factory.py` (`options_quality`) |
| Per-ticker veto / block | `executive_gatekeeper_{TICKER}_latest.json` + entry validator |
| Heat-cap / concentration pre-check | `research/portfolio_concentration_report.py` logic |
| Friction budget | `ROUND_TRIP_FRICTION_PCT` (`research/paper_trades/resolve_tactical_outcomes.py`) |
| Paper logging schema | `paper_signals` (incl. `setup_state_hash`, `aux_h3`) — additive only |
| Outcome resolution | `paper_signal_outcomes` (horizons 1/3/5/10) |

The build is mostly *glue + a reclaim trigger + a risk pre-check gate*, sitting on top of components that already run nightly. That is the core reason LEADER_RESET ranks #1 for "clean paper evidence in 30–60 days."
