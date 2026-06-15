# Executive Research Gatekeeper V1

Last updated: 2026-05-05.

> **Purpose.** A research-only final-review layer that synthesises existing
> system outputs (Stock Lens, Alpha Discovery, Daily Entry Validator,
> Market Forecast, options quality, FMP fundamentals cache, local
> portfolio state) into a deterministic verdict before a ticker is
> considered for **manual** trading research. This module produces a
> verdict and a plain-English summary; it produces **no** trade signal,
> no order, no hedge, no position size, and no sleeve promotion.

## What this is not

The gatekeeper does **not**:

- generate trade signals or paper-evidence rows;
- execute orders, hedges, or any provider-side action;
- use Kelly sizing or any sizing scheme tied to live capital;
- promote SNIPER / VOYAGER / SHORT_A / Alpha Discovery / Stock Lens / Daily Entry Validator / Market Forecast / paper governance / execution;
- mutate any other module's behaviour;
- call FMP, Tradier, or Alpaca (V1 reads only cache files + the local SQLite DB);
- let an LLM make the final decision — LLMs may **only** rephrase the deterministic verdict, never override it.

These are enforced by the structure: only `core/executive_gatekeeper.py` and `research/executive_gatekeeper_report.py` are touched. No imports of strategies, execution, governance, or scanner modules.

## Files

| Path | Role |
|---|---|
| `core/executive_gatekeeper.py` | Deterministic gate engine + LLM-summary hook. |
| `research/executive_gatekeeper_report.py` | CLI runner. |
| `cache/research/executive_gatekeeper_<TICKER>_latest.json` | Full structured result. |
| `logs/executive_gatekeeper_<TICKER>_latest.txt` | Plain-English summary. |
| `docs/research/EXECUTIVE_GATEKEEPER_V1.md` | This document. |

## CLI

```sh
cd /home/gem/trading-production
.venv/bin/python research/executive_gatekeeper_report.py --ticker AAPL
.venv/bin/python research/executive_gatekeeper_report.py --ticker NVDA --with-llm-summary
.venv/bin/python research/executive_gatekeeper_report.py --ticker AAPL --json
```

`--with-llm-summary` is optional. When set, the gatekeeper attempts an Anthropic-API call to produce a plain-English description. If `ANTHROPIC_API_KEY` is missing or the call fails, the gatekeeper falls back to a deterministic prose summary written from the gate results — the **deterministic verdict is unchanged either way**.

## Inputs (read-only, cache-first)

| Source | Path / origin | Used by |
|---|---|---|
| Stock Lens artefact | `cache/research/stock_lens_<TICKER>_latest.json` | gates 1, 2, 3, 5, 7 |
| Market Forecast | `cache/research/regime_forecast_latest.json` | gates 2, 7 |
| Alpha Discovery board | `cache/research/alpha_discovery_board_latest.json` | gates 1, 5 |
| Alpha Discovery overlay | `cache/research/alpha_discovery_overlay_latest.json` | gates 1, 5 |
| FMP fundamentals cache | `cache/fundamentals/<TICKER>.json` | gate 4 |
| Local portfolio state | `db/trading.db` (paper_signals + voyager_paper_signals) | gate 6 |

If any source is missing, the corresponding gate returns `MISSING` (not a crash). The aggregator translates a high enough missing-count into `INSUFFICIENT_DATA` rather than over-confident PASS.

## The 7 deterministic gates

Each gate returns a `GateResult` with one of these verdicts:
`PASS`, `CAUTION`, `DOWNGRADE`, `BLOCK`, `MISSING`.

### 1. Entry Quality — `gate_entry_quality`

- Source: Stock Lens `layers.entry_validator` (preferred), falls back to Alpha Discovery row's `validator_state`.
- `Too Extended` / `Broken` / `Avoid` → **BLOCK**.
- `Watch Reclaim` / `Wait for Confirmation` / `Watch` / `Neutral` → **DOWNGRADE**.
- `Actionable Now` / `Buyable` / `Ready` → **PASS**.
- Anything else (or unfamiliar wording) → **CAUTION**.
- **Options activity cannot override a bad entry.** This is enforced in `note`, but practically by the fact that Gate 3 cannot upgrade Gate 1's verdict.

### 2. Regime / Sector — `gate_regime_sector`

- Source: Market Forecast `headline.current_regime / bias_5d / bias_10d`, `volatility.vix`; Stock Lens `layers.sector` and `layers.technicals`.
- Risk-off market context (regime contains `"stress"`, bias_5d/10d in `{defensive, bearish}`, VIX ≥ 25) → **DOWNGRADE**.
- Sector weakening (`view` in `{weakening, lagging, rolling over}` or `rs_vs_spy_10d < −1.0`) → **DOWNGRADE**, unless the stock outperforms its sector by > +1.0pp 10d-relative-to-SPY → **CAUTION** (partial offset).
- Otherwise PASS, with the contextual evidence in `evidence`.

### 3. Options / Whale Quality — `gate_options_quality`

- Source: Stock Lens `layers.options.options_quality`.
- Vocabulary mapping:
  - `BULLISH_CONFIRMING` → **PASS**
  - `BULLISH_BUT_LATE` → **CAUTION**
  - `MIXED_OPTIONS` → **CAUTION**
  - `BEARISH_HEDGE` → **DOWNGRADE**
  - `OPTIONS_NO_EDGE` → **CAUTION**
  - `SPECULATIVE_CALL_CHASE` → **BLOCK**
  - missing layer → **MISSING** (treated as no edge, not as bullish).
- The gate explicitly does **not** treat raw call activity as bullish; only `options_quality` (which already accounts for OI tilt, vol tilt, IV skew, expiry confirmation, spread quality) is consumed.

### 4. Fundamental / Moat — `gate_fundamental_moat`

- Source: FMP fundamentals cache (4 quarters → TTM).
- Components scored 0–1 each, then averaged:
  - ROE (TTM net income / latest equity), normalised over [0.05, 0.30]
  - ROIC proxy (TTM EBIT / (equity + total debt)), normalised over [0.05, 0.25]
  - Debt-to-equity, normalised inversely over [0, 2.5]
  - Revenue YoY (latest Q vs same-Q-prior-year), normalised over [−0.05, +0.25]
  - Margin trend (gross-margin TTM vs gross-margin TTM(−4)), normalised over [−0.03, +0.05]
  - FCF / NI ratio (TTM), normalised over [0.5, 1.5]
- Quality label: ≥ 0.70 → strong (PASS); ≥ 0.45 → acceptable (CAUTION); ≥ 0.20 → weak (DOWNGRADE); < 0.20 → poor (BLOCK).
- Components with no input are dropped, not assumed-zero. If no components are usable → **MISSING**.
- **No DCF.** V1 deliberately does not build a point-estimate intrinsic value; the spec is "do not over-rely on DCF".

### 5. Institutional / Insider Context — `gate_institutional_insider`

- Source: Stock Lens `layers.institutional` (`available` flag) and Alpha Discovery row's `sponsorship_score` + `crowd_penalty`.
- Sponsorship buckets:
  - ≥ 70 → PASS (heavy institutional support)
  - 50–69 → CAUTION (moderate)
  - 30–49 → CAUTION (light)
  - < 30 → DOWNGRADE (sparse)
- Crowd penalty ≥ 2.0 demotes PASS to CAUTION.
- 13F is treated as **background context only** — the gate is explicit that "13F is never used as short-term timing".
- V1 does not call FMP for live insider/institutional snapshots. If neither cache shows institutional information → **MISSING**.

### 6. Portfolio Risk — `gate_portfolio_risk`

- Source: `db/trading.db` `paper_signals` (status='open') + `voyager_paper_signals`.
- **BLOCK** if the ticker already has an open paper position (do not stack research).
- **BLOCK** if total open paper signals ≥ 10 (any sleeve), or if open positions in the ticker's sector ≥ 4.
- **CAUTION** at 6 total open or 2 open in the same sector.
- The sector match is exact case-insensitive name match — open positions whose `sector` is empty/unknown do **not** falsely concentrate every ticker (a bug caught in V1 dev and fixed).

### 7. Tail Risk / Hedge Suggestion — `gate_tail_risk`

- Source: Market Forecast `volatility.vix` / `vix_avg_20` / `vix_change_5d`; Stock Lens `layers.options.iv_skew / atm_put_iv`.
- VIX ≥ 30 → **DOWNGRADE**, suggest "defer entry; OTM put protection if already exposed (research-only)".
- VIX ≥ 22 or VIX above its 20-day average, or `atm_put_iv > 0.40` → **CAUTION**, suggest "small OTM put or put-spread overlay (research-only)".
- Otherwise **PASS** ("benign tail-risk environment").
- Hedge suggestions are **research-only**. No order placement, no Kelly sizing, no execution.

## Aggregation → final status

```
final_status ∈ { PASS_RESEARCH, WATCH, BLOCK, INSUFFICIENT_DATA }
confidence   ∈ { low, medium, high }
sizing       ∈ {
  "no size — at least one blocking gate fired",
  "no size — insufficient data to grade research",
  "small research size only (paper / manual notebook) — conditions are mixed",
  "normal research size (paper / manual notebook only — no live capital allocation, no Kelly sizing)"
}
```

Rules (in order):

1. Any gate verdict is `BLOCK` → `final_status = BLOCK`. Sizing: "no size".
2. ≥ 4 of 7 gates are `MISSING` → `final_status = INSUFFICIENT_DATA`. Confidence: low.
3. Cumulative non-block severity (PASS=0, CAUTION=1, DOWNGRADE=2) ≥ 3, **or** at least one DOWNGRADE, **or** ≥ 2 missing → `WATCH`. Confidence: high → medium → low as missing count rises.
4. Otherwise → `PASS_RESEARCH`. Confidence: high if no missing, medium with 1 missing, low with ≥ 2 missing.

This is a strictly deterministic mapping from `(gate verdicts × missing count)` to `(final_status, confidence, sizing)`. No randomness. No LLM input. No floating-point fragility.

## LLM role (descriptive only)

When `--with-llm-summary` is set:

1. The deterministic `GatekeeperResult` is fully constructed first.
2. A serialised copy of the result is passed to the LLM with an instruction to **restate** the deterministic verdict and rephrase the evidence in plain English (≤ 200 words).
3. The LLM-generated string is attached to `result.llm_summary`. **No other field on the result is mutated.**
4. If the LLM call fails for any reason (no `ANTHROPIC_API_KEY`, network error, SDK absent, JSON parsing error) the gatekeeper transparently falls back to a deterministic prose summary built from the gate results.

The LLM cannot:

- change `final_status`, `confidence`, or `sizing_guidance`;
- add or remove gates;
- inject new evidence not already in the deterministic result.

These constraints are enforced by **the order of operations in `run_executive_gatekeeper`**: the LLM is given a finalised result object and only the `llm_summary` field is writable thereafter.

## Output artefacts

For ticker `AAPL`:

- `cache/research/executive_gatekeeper_AAPL_latest.json` — full structured result, including each `GateResult` with its `evidence` dict (so a downstream reader can audit exactly what the gatekeeper saw).
- `logs/executive_gatekeeper_AAPL_latest.txt` — the plain-English summary (LLM-generated when configured, deterministic prose otherwise).

Both artefacts are **idempotent overwrites** — every run replaces the previous file.

## Sample output (AAPL, 2026-05-05)

```
Final status:   WATCH
Confidence:     medium
Sizing:         small research size only (paper / manual notebook) — conditions are mixed

Gate verdicts:
  · entry_quality           DOWNGRADE   Daily Entry Validator says 'Watch Reclaim'…
  · regime_sector           PASS        market Bull Continuation; sector XLK Improving (rs10=2.50)
  · options_quality         PASS        options_quality=BULLISH_CONFIRMING (broad confirmation)
  · fundamental_moat        PASS        quality strong (ROE 1.15, ROIC 1.36, FCF/NI 1.05)
  · institutional_insider   MISSING     no institutional / insider context in cached artefacts
  · portfolio_risk          PASS        portfolio exposure ok
  · tail_risk               CAUTION     VIX=18.29 elevated (avg20=17.90)

Hedge suggestion (research-only, no order placement): consider a small OTM put or put-spread overlay if conviction is otherwise high.
```

## Limitations

- **Cache-only.** V1 reads `cache/research/`, `cache/fundamentals/`, and `db/trading.db`. There is no fallback path that calls FMP / Tradier directly; if a Stock Lens artefact is stale or absent, the gatekeeper degrades gracefully but cannot synthesise replacement data.
- **No insider live-feed.** FMP insider transactions are not cached today; `gate_institutional_insider` therefore relies on Alpha Discovery's `sponsorship_score` (a coarse 13F-derived signal) and the Stock Lens institutional layer's `available` flag. A future V2 could thread cached FMP insider snapshots without changing the public surface.
- **Sector mapping is best-effort.** The portfolio gate uses the Stock Lens `sector_name` to count open-position concentration. If `paper_signals.sector` is blank for an open row, that row contributes to "UNKNOWN" and **does not** falsely inflate any specific sector — but the stated concentration is therefore a lower bound.
- **Fundamental scoring is intentionally coarse.** Six normalised components, equally weighted. There is no DCF, no peer-relative valuation. The label is a 4-bucket grade, not a price target.
- **The LLM summary path is best-effort.** The deterministic verdict is the contract; the LLM string is a display annotation. Tools downstream should read `final_status`, never the LLM prose, for any control-flow decision.
- **No back-fill.** Existing paper signals or research notebooks are not retroactively gated.
- **V1 does not run automatically.** The CLI is the only entry point. There is intentionally no scheduler integration, no dashboard side-panel, no daemon. A future phase can wire in a cron or a notebook helper without changing the gatekeeper's public surface.

## Verification (recorded 2026-05-05)

- `py_compile` passes on `core/executive_gatekeeper.py` and `research/executive_gatekeeper_report.py`.
- Run on five sample tickers — AAPL (full cache → WATCH), NVDA (full cache → WATCH), SNPS (Alpha Discovery shows Too Extended → BLOCK), ROKU (overlay shows Too Extended → BLOCK), ZZZZ (no cache → INSUFFICIENT_DATA).
- LLM-cannot-override test: with `with_llm_summary=True` and no `ANTHROPIC_API_KEY` set, SNPS still resolves to `final_status=BLOCK`; the prose fallback restates "BLOCK" verbatim.
- No edits to `strategies/`, `core/sniper*`, `core/alpha_discovery.py`, `core/daily_entry_validator.py`, `core/regime_forecaster.py`, `core/stock_research_lens.py`, `core/paper_validation.py`, `execution/`, `dashboards/`, or any scanner — the gatekeeper is a side-car.
- No provider calls observed during sample runs; all reads are SQLite + JSON cache.
