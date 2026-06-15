# Options Data Inventory (Phase 1J.0)

Generated: 2026-06-12T21:34:34.186261+00:00

RESEARCH_ONLY: cache-only audit; no provider calls, no signals, no orders, no proposals.

## CAN WE VALIDLY BACKTEST OPTIONS PREMIUM NOW? NO

Only current chain snapshots are served (never persisted); there is no historical strike-level data, no bid/ask history, no OI history, and the retained ATM-IV series is too young even for a partial IVR. A trade-level options premium backtest cannot be modeled honestly today.

## What exists locally

| Dataset | Granularity | Symbols | Window | Depth |
|---|---|---:|---|---|
| `cache/research/options_iv_history.jsonl` | ATM IV blend + skew per (ticker, date, bucket) | 355 | 2026-04-30 → 2026-06-12 | best 24d, median 2d |
| `data/research/options_regime_lens_history.jsonl` | market IV/skew/term/gamma-proxy | 4 (IWM, QQQ, SPY, VXX) | 2026-05-28 → 2026-06-12 | {'IWM': 15, 'QQQ': 15, 'SPY': 15, 'VXX': 15} |
| Strike-level chains (bid/ask, OI, greeks, expirations) | — | 0 | none | **not persisted anywhere** |

Point-in-time status: both retained series are append-only from live chains (future-dated rows: 0); there is NO historical backfill source. Current snapshots are explicitly NOT treated as history.

Providers: Alpaca snapshots carry no IV/greeks/OI (OI merged live from the contracts endpoint); Tradier serves current chains only. Realized volatility is computable (192 deep-cache tickers, ~None bars) — IV history is the constraint.

## IV Rank feasibility (Task 3)

Overall: **IVR_NOT_VALID** — best symbol has 24 IV days; 36 more days to PARTIAL, 96 to FEASIBLE.

| Symbol | IV days | Span | Missing rate | Verdict |
|---|---:|---|---:|---|
| QQQ | 24 | 2026-05-04 → 2026-06-12 | 0.2 | IVR_NOT_VALID |
| SPY | 18 | 2026-05-14 → 2026-06-12 | 0.1818 | IVR_NOT_VALID |
| IWM | 17 | 2026-05-14 → 2026-06-12 | 0.2273 | IVR_NOT_VALID |
| DIA | 17 | 2026-05-14 → 2026-06-12 | 0.2273 | IVR_NOT_VALID |
| NXPI | 16 | 2026-05-12 → 2026-06-07 | 0.1579 | IVR_NOT_VALID |
| LSCC | 15 | 2026-05-04 → 2026-06-10 | 0.4643 | IVR_NOT_VALID |
| GOOGL | 15 | 2026-05-06 → 2026-06-12 | 0.4643 | IVR_NOT_VALID |
| SBAC | 14 | 2026-05-01 → 2026-05-26 | 0.2222 | IVR_NOT_VALID |
| AAPL | 14 | 2026-05-01 → 2026-06-12 | 0.5484 | IVR_NOT_VALID |
| STLD | 14 | 2026-05-04 → 2026-06-12 | 0.5333 | IVR_NOT_VALID |

## Strategy feasibility map (Task 4)

| Strategy | Verdict | Why |
|---|---|---|
| A_iron_condor_30_60_dte | **NOT_FEASIBLE_WITH_CURRENT_DATA** | 4-leg defined-risk structure needs historical chains with per-strike bid/ask and OI; no point-in-time strike-level chains, no bid/ask history, no OI history — fills cannot be modeled honestly |
| B_put_credit_spread_30_60_dte | **NOT_FEASIBLE_WITH_CURRENT_DATA** | 2-leg spread needs the same per-strike history; no point-in-time strike-level chains, no bid/ask history, no OI history — fills cannot be modeled honestly |
| C_cash_secured_put | **NOT_FEASIBLE_WITH_CURRENT_DATA** | premium capture cannot be priced without historical quotes; an equity-proxy backtest would not be honest premium evidence |
| D_covered_call | **NOT_FEASIBLE_WITH_CURRENT_DATA** | same as C — call premium history does not exist locally |
| E_ivr_signal_only_no_execution | **FEASIBLE_WITH_LIMITATIONS** | ATM-IV history is accumulating point-in-time (best symbol 24 days) but is below the 60-day partial floor; the signal becomes testable as the existing collection keeps running — no new build required, only time |
| F_volatility_regime_diagnostic_only | **FEASIBLE_NOW** | already exists: Phase 1G.12 options regime lens (SPY/QQQ/IWM/VXX IV, skew, term structure, naive gamma proxy) with a short but honest point-in-time history; diagnostic only, never a trade signal |

30–60 DTE backtesting possible: **False**. 45 DTE possible: **False**. no historical expirations/strikes are retained; a single 45 DTE trade cannot even be priced at entry, let alone marked daily.

## Paths forward

- To PARTIAL: keep the existing Stock Lens IV collection running; PARTIAL unlocks at 60 days of IV history for liquid symbols
- To YES: requires persisting point-in-time strike-level chains (bid/ask, OI, expirations) from the live feed going forward, or licensing historical chain data

## Safety

Cache-only audit. No live trading, broker orders, paper signals, trade proposals, strategy activation,
production changes, directional-module deletion, or evidence mutation. SHORT_A remains frozen.
The Task 7 skeleton was NOT built: research/options_premium_research_lab.py is built only on YES or strong PARTIAL; not earned.

