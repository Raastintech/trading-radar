# Retail Edge Hardening

Last updated: 2026-05-04 (Phase 10).

This document is a clear, retail-practical inventory of the "edge" layers in
this system. The goal is not to mimic an institutional quant desk. The goal is
to stay disciplined, avoid late-chase behavior, and trade only when several
honest signals agree.

These layers were already in place. Phase 10 just labels each one with what it
*can* do and what it *cannot* do, so the system does not mislead a retail
trader into overconfidence.

---

## The hard rule

> **No single layer is trade approval.**
> Every layer below can confirm or warn. None of them can override poor entry
> location, poor risk geometry, or a regime that disagrees.

A trade requires:
1. The strategy's structural setup (price/volume + mandate-aligned signal).
2. Entry location that respects R:R and stop geometry.
3. No active veto from the council.
4. Regime/posture is not in a hard veto state.

Any "edge" layer that does not show up in those four checks is supplementary —
useful for context, never sufficient on its own.

---

## Layer inventory

### 1. Whale / options activity — *Stock Lens options quality layer*

Path: `research/stock_lens_runner.py` + `research/stock_lens_forward_report.py`

**What it can do**
- Flag unusual flow that often precedes catalyst-driven moves.
- Confirm that institutional participants (not just retail) are positioning.
- Warn when option skew or term structure disagrees with price action.

**What it cannot do**
- Time entries. Flow can show up days or weeks early.
- Distinguish hedging from directional intent on its own.
- Replace structural confirmation. A whale buying calls on a name that is
  extended into resistance is still a bad entry.

**How to use**
- As a *confirmation/warning* layer, not as a primary signal.
- If the lens disagrees with the strategy thesis (e.g., bearish put flow on a
  long breakout candidate), treat that as a yellow flag and require stronger
  structural confirmation before taking the trade.

### 2. Institutional movement — *13F overlay (Voyager Mode B)*

Path: `research/backtests/voyager_v2_backtest.py` (Mode B), production
strategies/voyager.py.

**What it can do**
- Show whether tracked funds (Vanguard, Berkshire, ARK, Citadel, etc.) were
  net buyers or sellers across recent quarters.
- Provide *background context* — "this is a name with multi-quarter
  accumulation" vs. "this is a name being distributed."

**What it cannot do**
- Time entries. 13F filings are reported with a 45-day lag and reflect
  positions from up to 90 days earlier.
- Distinguish forced flows (index rebalancing, fund redemptions) from
  conviction trades.
- Replace fundamental + technical confirmation. A name that fund-X bought last
  quarter can still be in a downtrend now.

**How to use**
- As *slow background context*. Use to add or subtract a confidence margin to
  a setup that already passed structural and fundamental gates.
- Never as a trigger.

### 3. Market / sector regime — *Strategic lens*

Path: `core/market_regime.py`, `research/regime_forecast.py`,
`council/macro_veto_agent.py`.

**What it can do**
- Tell you when the broad tape favors trend (low VIX, SPY above 200d) vs.
  when it favors mean reversion (high VIX, SPY below 200d).
- Flag sector rotation that disagrees with a sleeve's thesis.
- Veto trades during macro events (FOMC days, NFP, CPI windows).

**What it cannot do**
- Predict the next move with usable precision.
- Override a high-quality setup *just* because the regime is mixed.

**How to use**
- As a *gate*: hard veto when regime is hostile (e.g., SNIPER won't long-trade
  when SPY < 200d MA; SHORT won't short-trade in a clear risk-on regime).
- As a *sizing input*: smaller positions when regime is ambiguous.

### 4. Daily Entry Validator — *Entry truth check*

Path: production daily validator (see `docs/strategy/CURRENT_DOCTRINE_MAP.md`).

**What it can do**
- Confirm that the bar that triggered the signal still meets the doctrine on
  re-evaluation (no cherry-picking intraday spikes).
- Catch stale or revised data (e.g., a volume spike that was later corrected
  down).

**What it cannot do**
- Predict whether the entry will work.
- Replace the strategy's own structural test.

**How to use**
- As the last gate before paper execution. If the validator says "no longer
  valid", do not enter even if the council approved earlier in the day.

### 5. Alpha Discovery — *Idea discovery*

Path: `research/alpha_discovery_board.py`.

**What it can do**
- Surface candidate setups across sleeves that are not yet on the active
  watchlist.
- Cross-validate that a name is showing up in multiple structural lenses.

**What it cannot do**
- Approve a trade. The board is a *suggestion engine*, not a decision engine.

**How to use**
- For research review and watchlist seeding. A name that shows up on the
  alpha board still has to pass the strategy's own scanner before paper
  execution.

### 6. Market Posture — *Current tape*

Path: dashboards/gem_trader_hq.py (MARKET POSTURE section).

**What it can do**
- Provide a single read of breadth, sector leadership, and intraday tone.

**What it cannot do**
- Tell you what to trade. Posture is a thermometer, not a prescription.

**How to use**
- As a *don't fight the tape* guard. If the posture is decisively risk-off,
  delay long entries even if a single setup looks good.

### 7. Social Arbitrage Radar — *Low-cost side radar*

Path: `research/social_arb_radar.py`.

**What it can do**
- Flag names with unusual social/news/search-trend acceleration.
- Surface narrative shifts before price action confirms them.

**What it cannot do**
- Be a primary entry signal. Social/news is noisy and frequently late.
- Distinguish organic interest from coordinated promotion.

**How to use**
- As a *side radar* for retail-driven names where momentum can extend further
  than fundamentals justify. Useful for short candidates (late-chase
  exhaustion) more than long candidates.
- Never trade purely on a social-radar hit.

### 8. Paper / forward tracking — *Evidence truth layer*

Path: `db/trading.db` (`paper_signals` + `paper_signal_outcomes` +
`voyager_paper_signals`), `research/strategy_evidence_audit.py`.

**What it can do**
- Provide ground truth on what the system actually scored, what the council
  decided, and what happened next.
- Power the rigor audit (CIs, walk-forward, random control).

**What it cannot do**
- Replace historical backtest depth. Live paper accumulates slowly; on its
  own it cannot answer "would this have worked over five years."

**How to use**
- As the *only* source of truth for sleeve promotion decisions.
- Combined with historical backtest exports
  (`research/sleeves/trades/*.csv`) for rigor analysis.

---

## Anti-patterns we explicitly avoid

- **Trading on whale flow alone.** "Big call buyer in X" is not a buy signal.
- **Trading on a single 13F print.** Position changes are reported with up to
  a 90-day lag; treat them as background, never as a trigger.
- **Trading on social/news heat alone.** This is what late chasers do, and
  it's exactly the cohort the SHORT sleeve targets.
- **Treating "promising but thin" as "robust."** The rigor audit explicitly
  separates these. A sleeve with n=16 closed trades cannot have a confident
  edge claim no matter how flattering the point estimate looks.
- **Promoting any sleeve to capital before the rigor audit clears.** Paper
  governance is the gate. The verdict in
  `docs/scorecards/evidence_rigor_report.md` is the truth.

---

## Cross-references

- `docs/strategy/STRATEGY_DOCTRINE.md` — sleeve mandates
- `docs/strategy/CURRENT_READINESS.md` — current platform status
- `docs/scorecards/evidence_rigor_report.md` — bootstrap/walk-forward/random control
- `research/strategy_evidence_audit.py` — audit pipeline
- `research/sleeves/export_backtest_trades.py` — trade-level export pipeline
