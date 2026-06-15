# Options Data Sources

Operational reference for the research-only options layer that feeds the
Stock Lens, Alpha Discovery overlay, and Social Arb radar. Captures
which provider serves which field, the known gaps, and how to diagnose
"thin chain" symptoms (e.g. `OPTIONS_NO_EDGE` on a name with obviously
healthy options activity).

**Doctrine:** options data is **research-only**. The execution path
remains Alpaca (data + execution) + FMP (fundamentals/events/macro/VIX)
per the `CLAUDE.md` provider policy. Options feeds never reach the
order manager; they only populate the Stock Lens `options` layer and
the Alpha overlay.

## Provider chain

`core/options_feed_factory.py:load_options_feed()` returns an
`OptionsFeedChain` with this order:

| Order | Feed | Status (today) | Activates when |
|---|---|---|---|
| 1 | `core.alpaca_options_client.AlpacaOptionsFeed` | **Active** — Pro+ data plan | always (uses `ALPACA_API_KEY`/`ALPACA_SECRET_KEY`) |
| 2 | `legacy.tradier_options_feed.TradierOptionsFeed` | **Active** (validated 2026-05-21) — serves IV + greeks | `TRADIER_API_TOKEN` set and token validates (no 401) |

Chain semantics: "first non-empty result wins" for `get_expirations`
and as the *primary* selection in `get_chain`. A leaf that returns
`None`, an empty tuple, or `{"calls": empty_df, "puts": empty_df}` is
treated as empty and the chain tries the next leaf. Exceptions are
caught per-feed; the chain never raises into the caller.

**Options Data Enrichment — Alpaca primary + Tradier IV/Greeks merge
(shipped 2026-05-21).** `get_chain` now also opportunistically
*enriches* the primary chain with IV + greeks from a secondary leaf
when the primary stubs those fields at 0. Note: this is the
cross-cutting options-data layer; it is **not** Phase 2C in the
roadmap. Phase 2C (Trade Proposal Generator — no execution) remains
reserved for future work. Rules:

- The merge fires only when at least one row on the primary chain has
  `impliedVolatility == 0`. Tradier-served chains (already complete)
  and Alpaca-only deployments (no secondary leaf) pay zero overhead.
- Per-row, non-destructive: a primary value that is already non-zero
  is never overwritten. Only the stubbed-zero rows are filled.
- Only `impliedVolatility`, `delta`, `gamma`, `theta`, `vega`, `rho`,
  `bid_iv`, `ask_iv` are mergeable. OI, volume, bid, ask, strike,
  `inTheMoney`, `contractSymbol` stay with the primary.
- The primary feed's response cache is isolated by copy — the merge
  cannot pollute a cached DataFrame returned by reference.
- Diagnostics: `OptionsFeedChain.status()` exposes `last_served`,
  `served_counts`, `last_enriched_by`, `enrich_counts`, plus
  `last_zero_iv_before` / `last_zero_iv_after` (per-side row counts
  of stubbed-IV rows immediately before/after the most recent
  `get_chain`). Counters reset to `None` when the chain query
  returned `None`.

Real-chain verification on TSLA (2026-05-21): IV coverage on calls
moved from 35/167 (Alpaca only) to 131/167 after Tradier enrichment;
delta from 0/167 to 102/167; OI preserved at 163/167 (Alpaca).

## Endpoint map

### Alpaca (current primary)

| Field family | Endpoint | Authority | Notes |
|---|---|---|---|
| Expirations (per underlying) | `GET {ALPACA_BASE_URL}/v2/options/contracts` | Trading API | Filtered by `expiration_date_gte/_lte`. Used by `get_expirations()`. |
| Chain quotes / trades / bars | `GET {ALPACA_DATA_URL}/v1beta1/options/snapshots/{underlying}` | Data API | Returns `latestQuote`, `latestTrade`, `dailyBar`, `minuteBar`, `prevDailyBar`. **No OI, no greeks, no IV.** |
| Open interest + close price | `GET {ALPACA_BASE_URL}/v2/options/contracts` | Trading API | Returns `open_interest` (as JSON string), `open_interest_date`, `close_price`. **Authoritative source** for OI on this plan. |
| Greeks (delta/gamma/theta/vega/rho) | _not available_ | — | Pro+ data plan does not expose greeks via either `feed=opra` or `feed=indicative`. See "Gaps" below. |
| Implied volatility | _not available_ | — | Same as greeks. |

`get_chain(ticker, expiry)` fetches **both** endpoints and merges:
snapshots provide quote/trade/bar; contracts provide OI + close_price
fallback. The adapter writes each row in the Tradier-compatible shape
(`strike, bid, ask, volume, openInterest, impliedVolatility,
inTheMoney`, plus greeks/IV stubs that are 0 on Alpaca-served paths).

### Tradier (active — validated 2026-05-21)

| Field family | Endpoint | Notes |
|---|---|---|
| Expirations | `GET /v1/markets/options/expirations` | Sorted ISO date tuple. |
| Chain (quotes + greeks + IV) | `GET /v1/markets/options/chains?greeks=true` | Returns delta, gamma, theta, vega, rho, mid_iv, bid_iv, ask_iv per contract. |
| Real-time quote | `GET /v1/markets/quotes` | Equity quote — not currently consumed by the lens. |

Tradier returns **IV and greeks inline** with the chain — this is the
only thing that makes the lens IV-rank / IV-skew / multi-expiry IV
features useful. Validated 2026-05-21 against TSLA front expiry:
non-empty `delta`, `gamma`, `theta`, `vega`, `mid_iv` per contract.

**Tradier IV + greeks now reach the lens (Options Data Enrichment,
shipped 2026-05-21).** `OptionsFeedChain` uses primary-served +
secondary-enrichment for `get_chain`: Alpaca still serves the chain
shape (OI, quote, volume), and Tradier fills `impliedVolatility` +
greeks on the stubbed rows. The lens / Alpha overlay / Social Arb
radar see merged rows without any code change on their side. See
"Chain semantics" above. (Distinct from roadmap Phase 2C, which is
reserved for the future Trade Proposal Generator.)

## Known gaps

### Alpaca Pro+ has no greeks/IV
Confirmed via live probe on both `feed=opra` and `feed=indicative`. The
snapshot response contains only quote/trade/bar fields. This means:

- `impliedVolatility` is `0` in every Alpaca-served row.
- `_atm_iv_from_chain()` in `research/stock_lens_runner.py` returns
  `None` → ATM IV blend is `None` → IV-rank/percentile are skipped.
- The classifier still produces `BULLISH_CONFIRMING /
  BULLISH_BUT_LATE / SPECULATIVE_CALL_CHASE / MIXED_OPTIONS /
  BEARISH_HEDGE / OPTIONS_NO_EDGE` based on OI-tilt + volume-tilt +
  spread quality. The label set is unchanged; the IV-context layer is
  what degrades.

Recovery paths (status updated 2026-05-21):

1. ✅ **Tradier activation + per-field merge.** Done — Tradier validates,
   and the chain wrapper now merges Tradier IV + greeks onto Alpaca
   rows where Alpaca stubs them at 0. See "Chain semantics" above for
   the merge rules.
2. Higher Alpaca tier (e.g. Algo Trader+) if it exposes greeks. Not
   verified; would require a fresh live probe. Not needed now that
   path #1 is live.
3. Compute Black-Scholes IV in-process from bid/ask + spot + risk-free
   rate. Doable, but adds a dependency boundary. Reserve as a fallback
   if Tradier coverage becomes unreliable.

### Bug history: `OPTIONS_NO_EDGE` on liquid names (TSLA, etc.)
**Symptom (pre-fix):** very liquid names (TSLA, SPY-class) landed on
`OPTIONS_NO_EDGE` with notes `OI 1.00 · vol X · liq thin`. Diagnosis:
the original `AlpacaOptionsFeed.get_chain()` only hit the snapshot
endpoint, which doesn't carry OI — every row was OI=0, so
`_filter_liquid_strikes` (`OI ≥ 50 OR volume ≥ 25`) discarded most
strikes, leaving the classifier with too-thin input.

**Fix (2026-05-17):** `get_chain()` now hits the contracts endpoint
alongside snapshots and merges OI per-row. Confirmed on TSLA: max call
OI 6,929 / max put OI 13,699 on the front Monday expiry. Label moved
from `OPTIONS_NO_EDGE` to `BULLISH_CONFIRMING (OI 1.42, vol 2.31,
spread good, liq ok)`.

Regression tests live in
`tests/unit/test_alpaca_options_client.py::test_get_chain_pulls_oi_from_contracts_endpoint`
and three sibling tests covering close_price fallback, contracts-API
failure degradation, and JSON-string coercion.

### Tradier 401 auto-disable
If `TRADIER_API_TOKEN` is set but the account is not activated, the
first request returns 401. The feed logs one warning
(`Tradier returned 401 for /v1/markets/options/... — auto-disabling`)
and marks itself unconfigured for the rest of the process. No spam,
no further calls. Re-runs of the daemon / scripts probe again.

Resolved 2026-05-21 — the operator rotated the production token and
`/v1/user/profile` + `/v1/markets/options/expirations` both return 200.
Keep this section as the diagnostic path if the warning ever reappears
(rotated token, expired subscription, sandbox/prod confusion).

## Consumers

These modules pull options via `load_options_feed()`:

- `research/stock_lens_runner.py:_load_options_layer` — feeds the Stock
  Lens `options` layer (`core.stock_research_lens._options_layer`).
- `core/alpha_discovery.py:_tradier_overlay` — feeds the Alpha
  Discovery board's options overlay column. Local variable name
  preserved for back-compat; behaves identically with the chain.
- `research/social_arb_radar.py` — same pattern.

None of these care which leaf served the chain. Diagnostics (which
feed served what) are visible in `OptionsFeedChain.status()`:
`last_served`, `served_counts`.

## Operator activation checklist (Tradier)

Status: **all four steps complete (2026-05-21).** Tradier is active,
the Options Data Enrichment merge is shipped, and the lens consumes
Tradier IV + greeks via the chain wrapper. (This work is **not**
Phase 2C — that slot remains reserved for the Trade Proposal
Generator. See `docs/ROADMAP_PHASES.md`.)

1. ✅ Confirm token in `/home/gem/secure/trading.env` (do not check into
   the repo or echo to logs). Token rotated on 2026-05-21 (production,
   not sandbox); `/v1/user/profile` returns 200.
2. ✅ Smoke probe — one-off, cache-only:
   ```bash
   SNIPER_ENV_PATH=/home/gem/secure/trading.env .venv/bin/python -c "
   from core.options_feed_factory import load_options_feed
   c = load_options_feed()
   print('chain feeds:', c.status()['feed_order'])
   exps = c.get_expirations('TSLA'); print('exps:', exps[:3])
   if exps:
       ch = c.get_chain('TSLA', exps[0])
       print('rows:', None if ch is None else (len(ch['calls']), len(ch['puts'])))
       print('served by:', c.last_served)
   "
   ```
   2026-05-21 result: `feed_order=['alpaca', 'tradier']`, `served by:
   alpaca`, 167×167 rows on TSLA front expiry. Both leaves healthy;
   Alpaca serves because it answers first non-empty.
3. ✅ **Options Data Enrichment — chain wrapper promoted to per-field
   merge** (2026-05-21). `OptionsFeedChain.get_chain` now
   opportunistically merges IV + greeks from a secondary leaf onto
   stubbed-zero rows of the primary, non-destructively and per-strike.
   Live verification on TSLA: IV coverage moved from 35/167 to 131/167
   calls; on AAPL: 78/78. `last_enriched_by` + `enrich_counts` surface
   in `status()` for operator confirmation. 33 unit tests in
   `tests/unit/test_alpaca_options_client.py` cover the chain
   (including primary-cache isolation).
4. ✅ Provider table updated (2026-05-21).

## Live-trading gate (unchanged)

Nothing in this layer affects the live-capital gate. The gate lives in
`core/config.py` + `AlpacaClient.submit_*_order` and is governed by
`PAPER_TRADING`, `ALPACA_PAPER`, `ALLOW_LIVE_CAPITAL`, and
optionally `LIVE_CONFIRM_FILE`. Options data flowing through Alpaca
does **not** imply options trading approval; data and trading
permissions are gated separately on Alpaca's side.
