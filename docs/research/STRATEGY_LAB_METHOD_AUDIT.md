# Strategy Lab Method Audit

Generated: 2026-06-12T14:29:22.557915+00:00

RESEARCH_ONLY: no broker orders, no paper signals, no trade proposals, no registry/governance/execution/live-capital changes.

## Assumptions Found

| Item | Finding |
|---|---|
| entry_price_assumption | next trading bar open by default; falls back to close if open is missing or invalid |
| exit_price_assumption | intraday stop or target at configured stop/target price, otherwise close on max_hold day |
| position_sizing_assumption | independent-trade summaries implicitly treat every signal as a full independent trade; no capital allocation exists in Phase 1H.1 summaries |
| compounding_assumption | max drawdown compounds the sequence of trade returns in exit-order, not a dated portfolio equity curve |
| overlapping_trades_handling | overlapping signals are all retained and measured independently |
| max_concurrent_positions | none in Phase 1H.1 independent-trade mode |
| duplicate_ticker_handling | no duplicate-open ticker guard in Phase 1H.1 independent-trade mode |
| sector_concentration_handling | reported as concentration counters only; no sector cap is enforced in Phase 1H.1 |
| portfolio_exposure_cap | none in Phase 1H.1 independent-trade mode |
| benchmark_comparison_method | per-trade net return minus SPY/QQQ return over each trade's entry-to-exit window; buy-hold rows are separate window-level trades |
| drawdown_calculation_method | lab._max_drawdown compounds independent trade returns; benchmark buy-hold drawdown in Phase 1H.1 is not daily mark-to-market |
| slippage_cost_model | round-trip bps model: slippage both ways, one spread charge, optional commission both ways, and annualized borrow for shorts |
| short_return_signing | short raw return is (entry - exit) / entry; SPY/QQQ relative returns are sign-flipped for short comparisons |

## Methodology Findings

- Large Phase 1H.1 drawdowns are partly a construction artifact because independent trades can create uncapped overlapping exposure.
- The strategy logic still matters: portfolio caps can reduce drawdown, but they cannot make negative expectancy or poor timing acceptable.
- Benchmark fairness requires dated portfolio returns, daily benchmark drawdown, exposure/cash adjustment, and a separate fully invested benchmark table.
- Per-trade expectancy should not be compared directly with SPY/QQQ buy-hold total return.

## Window Construction

Exact-full includes yearly windows plus recent/rolling diagnostics, so aggregate trade counts and expectancy intentionally double-count calendar periods. Portfolio correction should use non-overlapping primary windows for deployable comparison and keep rolling windows diagnostic.

Primary non-overlapping windows: `2024_available, 2025_available, 2026_ytd`.

## Safety

No live trading, broker orders, paper signals, trade proposals, production threshold changes, Gatekeeper/Veto changes, live-capital changes, or historical evidence mutation.
