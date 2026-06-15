# Options Backtest Realism Checklist (Phase 1J.0)

Any future options backtest in this repository MUST model every item below, or explicitly
document why an item is skipped and label the result as degraded. A backtest that fills at
mid with no liquidity screen is not evidence; it is fiction.

RESEARCH_ONLY: this checklist authorizes nothing. No paper signals, no broker orders, no
trade proposals, no production changes.

## Fill realism
- [ ] **Bid/ask fills** — enter short premium at or below bid, exit at or above ask (or a
      documented fraction of the spread); never fill at mid without a sensitivity run.
- [ ] **Slippage** — explicit per-leg slippage on top of spread crossing; widen during
      high-volatility regimes.
- [ ] **Liquidity / OI filters** — minimum open interest and volume per leg; reject strikes
      with stale or crossed quotes; reject spreads wider than a max percentage of premium.

## Contract selection
- [ ] **DTE selection** — defined window (e.g. 30-60 DTE, 45 target) using actual listed
      expirations as of the entry date, not idealized calendars.
- [ ] **Strike selection** — rule-based (delta or percent-OTM) from the chain as it existed
      at entry; no strike interpolation that did not trade.
- [ ] **Delta selection (if Greeks exist)** — point-in-time Greeks only; if Greeks are
      derived (Black-Scholes from IV), label them DERIVED and sensitivity-test.

## Event and tail risk
- [ ] **Earnings blackout** — no short premium across earnings unless the strategy is
      explicitly an earnings strategy; requires point-in-time earnings dates.
- [ ] **Volatility expansion** — mark-to-market losses from IV spikes mid-trade, not just
      expiry P&L.
- [ ] **Gap risk** — overnight/weekend gaps through short strikes; no intraday stop fantasy
      on gapped opens.
- [ ] **Assignment risk** — ITM short legs near ex-dividend dates and at expiry.
- [ ] **Early assignment risk** — American-style early exercise on deep ITM short legs.
- [ ] **Correlation spike during corrections** — multiple short-premium positions lose
      together; portfolio drawdown must be measured on the joint path, not per-trade.

## Risk accounting
- [ ] **Max loss** — defined-risk width minus credit for spreads; full notional for
      cash-secured puts; uncapped short legs are out of scope.
- [ ] **Margin requirement** — buying-power reduction per position and portfolio-level;
      no-margin assumption must be stated if used.
- [ ] **Position sizing** — risk-based (max loss as % of equity), not contract-count.
- [ ] **Sector/underlying clustering** — caps on same-underlying and same-sector premium
      exposure (the 1I.2 lesson: clustering is where drawdowns hide).

## Exit discipline
- [ ] **Stop/exit rules** — defined loss multiple (e.g. 2x credit) and DTE-based exit
      (e.g. close at 21 DTE); tested, not assumed.
- [ ] **Profit-taking rules** — e.g. 50% of max profit; sensitivity-tested.

## Evidence standards (inherited from the directional arc)
- [ ] Point-in-time data only; no current-snapshot backfill.
- [ ] Walk-forward split with a binding test block.
- [ ] Same-exposure benchmark comparison (premium strategies vs T-bill + SPY mix).
- [ ] Month/ticker concentration checks.
- [ ] Independent-stream drawdown reported alongside capped portfolio drawdown.
- [ ] Pre-registered gates; no post-hoc threshold tuning.
