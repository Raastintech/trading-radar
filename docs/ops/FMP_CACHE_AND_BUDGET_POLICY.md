# FMP cache + budget policy (Phase 2B.4)

This document is the operating contract for Financial Modeling Prep
(FMP) API usage. It defines which callers may hit FMP, the TTL for
each cached endpoint, the refresh cadence baked into the research
cycle, and the invariants the dashboard must preserve. Anything not
listed here as a permitted caller MUST read from the on-disk cache —
no exceptions.

## Why this exists

We hit the FMP monthly bandwidth limit in April. The remediation
landed across Phases 1B/1C/1D and 2B.x:

- The `Gatekeeper` cache (`core/data_gatekeeper.py`) front-loads every
  FMP call. TTLs are tuned per endpoint to balance freshness against
  call count.
- The dashboard is **strictly cache-only** for all research artifacts
  it surfaces. The only FMP path the dashboard owns is the daemon
  layer's polling of low-frequency endpoints (`vix`, `treasury`,
  `econ_cal`, `earnings_cal`, `sector_pe`).
- The Phase 2B audit workflows (`research/mcp_audit_*`) and the new
  Phase 2B.4 `research/provider_freshness_audit.py` are read-only
  diagnostics — they verify state without driving load.

## TTL table (source of truth: `core/data_gatekeeper.py`)

| Cache prefix | TTL | Source | Rationale |
|---|---|---|---|
| `fmp:vix` | 5 min | `TTL_VIX` | Real-time-ish for regime gate. |
| `fmp:quote` | 20 s / 5 min AH | `TTL_QUOTE` / `TTL_QUOTE_AH` | Intraday quote cache; AH stretch keeps dashboards quiet overnight. |
| `fmp:bars` | 12 h | `TTL_OHLCV` | Per-ticker daily bars; same-day freshness is sufficient for strategy scans. |
| `fmp:spy_bars` | 12 h | `TTL_OHLCV` | SPY bars feed regime + MA computation. |
| `fmp:treasury` | 4 h | `TTL_TREASURY` | Risk-free rate inputs. |
| `fmp:econ_cal` | 4 h | `TTL_ECONOMIC_CAL` | Macro events panel; CPI/FOMC ticks need same-session freshness. |
| `fmp:news` | 1 h | `TTL_NEWS` | News headlines decay fast. |
| `fmp:earnings_cal` | **13 h** | `TTL_EARNINGS_CAL` | **Phase 2B.4 bump (was 6 h).** Covers nightly → premarket gap (~11.5 h) so the 08:00 ET premarket reads a warm cache populated by the 20:30 ET nightly. |
| `fmp:past_earnings` | 13 h | `TTL_EARNINGS_CAL` | Used by SHORT scanner for event triggers. |
| `fmp:profile` | 24 h | `TTL_FUNDAMENTALS` | Company profile / sector / industry. |
| `fmp:fundamentals` (income / balance / cash flow) | 24 h | `TTL_FUNDAMENTALS` | Quarterly statements — daily refresh is sufficient. |
| `fmp:grades` | 24 h | `TTL_FUNDAMENTALS` | Analyst grade history. |
| `fmp:insider` | 24 h | `TTL_FUNDAMENTALS` | Insider trade history. |
| `fmp:sector_pe` | 24 h | `TTL_FUNDAMENTALS` | Sector P/E ratios. |
| `fmp:dcf` | 7 d | (enumerated — not actively wired) | DCF valuations are noisy on shorter cadences. |
| `13f:v2` | 14 d | weekly cadence | 13F filings are quarterly; weekly refresh is more than enough. |

## Allowed callers

The audit treats anything outside this allow-list as a doctrine
violation. Each prefix lists the components permitted to trigger an
FMP fetch:

| Prefix | Allowed callers |
|---|---|
| `fmp:vix` | daemon, dashboard |
| `fmp:quote` | daemon, dashboard |
| `fmp:bars` | daemon, research |
| `fmp:spy_bars` | daemon, dashboard |
| `fmp:treasury` | daemon, dashboard |
| `fmp:econ_cal` | daemon, dashboard |
| `fmp:news` | daemon, dashboard |
| `fmp:earnings_cal` | daemon, dashboard, `gatekeeper-refresh` |
| `fmp:past_earnings` | strategies |
| `fmp:profile` | daemon, research |
| `fmp:fundamentals` | daemon, research, `gatekeeper` |
| `fmp:grades` | daemon, research |
| `fmp:insider` | daemon, research |
| `fmp:sector_pe` | daemon, dashboard |
| `fmp:dcf` | research |
| `13f:v2` | research |

The dashboard panel layer is **cache-only**: it reads JSON sidecars
under `cache/research/` and `cache/state/`, plus the daemon-driven
in-memory `DataLayer`. It never calls FMP directly. Same rule
applies to `research/mcp_audit_workflows.py`,
`research/mcp_audit_orchestrator.py`,
`research/paper_state_hygiene_report.py`, and
`research/provider_freshness_audit.py`.

## Refresh cadence (`scripts/run_research_cycle.sh`)

Phase 2B.3 wired Gatekeeper refresh into the existing cadence; Phase
2B.4 added the provider/pipeline audit as a nightly tail.

```
premarket (08:00 ET Mon-Fri)
  forecast → alpha → alpha-overlay → delta → gatekeeper-refresh

nightly (20:30 ET Mon-Fri)
  forecast → alpha → social → delta → lenses-nightly --max=35
    → gatekeeper-refresh → resolve → holdout → risk-telemetry
    → provider-audit

midday (12:30 ET Mon-Fri)
  resolve → reports → delta → risk-telemetry
  # Cache-only.  Gatekeeper-refresh is operator-triggered ad-hoc.

mcp-audit-session [open|regular|close] [--refresh-gatekeeper]
  # Cache-only by default; --refresh-gatekeeper opts in to a
  # pre-orchestrator Gatekeeper refresh.

provider-audit
  # Cache-only.  Read cache_meta / fmp_endpoint_log / budget tables
  # and the per-pipeline JSON sidecars; print a PASS/WARN/FAIL summary
  # and predict tomorrow's premarket FMP load.
```

### Freshness-first operator order

```
1. forecast / alpha / lens refresh       (provider)
2. gatekeeper-refresh                    (cache-first; FMP earnings cal only)
3. risk-telemetry                        (cache-only)
4. mcp-audit-session                     (cache-only)
5. dashboard review                      (cache-only)
```

`premarket` covers steps 1-2; `nightly` covers steps 1-3 plus the
Phase 2B.4 provider audit. Step 4 is operator-triggered. Step 5
is the operator's eyeballing — no automation.

## Emergency / manual refresh

The Phase 2B.2 dashboard surfaces stale Gatekeeper / MCP audit
panels with the exact rerun command. The operator may:

- `./scripts/run_research_cycle.sh gatekeeper-refresh --max 10` to
  refresh a smaller batch.
- `./scripts/run_research_cycle.sh gatekeeper-refresh --watch NVDA`
  to refresh a specific ticker.
- `./scripts/run_research_cycle.sh gatekeeper NVDA` to bypass the
  selector and call the underlying Executive Gatekeeper directly.
- `./scripts/run_research_cycle.sh mcp-audit-session regular
  --refresh-gatekeeper` to opt-in to a combined refresh + audit.

The dashboard NEVER calls a refresh on its own. It surfaces stale
artifacts and the rerun command so the operator can choose.

## Earnings cache: TTL = 13 h

The 6 h → 13 h bump (Phase 2B.4) is safe because:

- The earnings calendar is **mostly static** within a 13 h window.
  Companies rarely move earnings dates intraday.
- Both nightly (20:30 ET) and premarket (08:00 ET) hit
  `get_earnings_calendar` via `gatekeeper-refresh`. With TTL = 13 h,
  premarket reads a 11.5 h-old warm cache rather than refetching.
- The dashboard surfaces an **`EARNINGS DATA STALE`** marker when
  the underlying cache row exceeds 14 h (one hour past TTL), so a
  missed cycle is still visible.
- The Phase 2B.2 freshness contract continues to flag stale
  Gatekeeper artifacts on earnings days at a tighter 6 h threshold
  — that part is unchanged.

If a calendar change is suspected (rare), the operator can force a
refresh via `./scripts/run_research_cycle.sh gatekeeper-refresh`
which calls FMP for the calendar regardless of cache age.

## Dashboard invariant

**The dashboard never calls FMP for research artifacts.** All of
the following are read from on-disk JSON / SQLite cache:

- regime forecast, alpha discovery, stock lens, executive gatekeeper
- MCP audit sidecar (`mcp_analysis_latest.json`)
- risk telemetry sidecars (slippage / concentration / shadow / hygiene)
- paper-state hygiene + broker-positions snapshot
- holdout scoreboard
- the earnings calendar — read through the daemon's in-memory cache
  with the underlying Gatekeeper TTL of 13 h enforcing the
  refresh window

The dashboard's `DataLayer` does poll Alpaca/FMP for low-frequency
items (positions, VIX, SPY bars, treasury, econ cal, earnings cal,
sector PE). All of those go through the Gatekeeper cache. None of
them drives the Phase 2B research artifacts surfaced in Mode 2 / 3.

## Operator commands

```bash
# Audit ── what's stale, what's likely to refetch
./scripts/run_research_cycle.sh provider-audit
./scripts/run_research_cycle.sh provider-audit --ticker NVDA   # per-ticker

# Read the audit sidecar later
.venv/bin/python research/provider_freshness_audit.py --ticker NVDA --print

# Refresh ── batched
./scripts/run_research_cycle.sh gatekeeper-refresh
./scripts/run_research_cycle.sh gatekeeper-refresh --max 10
./scripts/run_research_cycle.sh gatekeeper-refresh --watch NVDA --max 10

# Refresh ── targeted
./scripts/run_research_cycle.sh gatekeeper NVDA

# Combined refresh + MCP audit
./scripts/run_research_cycle.sh mcp-audit-session regular --refresh-gatekeeper
```

## What this document does NOT govern

This is a **cache/budget policy** document. It does not:

- govern strategy logic, scoring, or veto council weights
- govern execution / order routing / live-capital gates
- mutate paper evidence or the database
- enable any new auto-execution path

Phase boundaries remain as documented in `docs/ROADMAP_PHASES.md` —
the live-capital gate in `core/config.py` is untouched. Operators
who need to change strategy behavior do so via the strategy and
scorecard docs under `docs/strategy/`, not here.
