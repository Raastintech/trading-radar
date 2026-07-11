# Legacy Universe Snapshot — Consumer Inventory (P0-B, 2026-07-10)

`cache/universe/universe_snapshot_latest.json` was written by the trading
daemon's `core/universe.py` builder every ~30 minutes. It froze at
**2026-06-12T19:34Z** when the daemon was decommissioned (2026-06-13) and
nothing rebuilds it. Any consumer treating it as current emits June-12
market state as if it were today's — the P0-B leakage this inventory closes.

Staleness rule: `core/market_session.py:source_staleness()` — the source is
stale when its `generated_at` predates the required completed trading
session (it is now permanently ~17+ sessions stale and only ages further).

## Live consumers (daily/weekly chains) — all repaired 2026-07-10

| Consumer | Used the snapshot for | Repair |
|---|---|---|
| `core/alpha_discovery.py` | Seed rows (per-symbol price/returns/volume **metadata** — the entire candidate universe and prelim ranking) + sleeve-resemblance labels from `strategy_candidates` | **MIGRATED**: seeds now come from the canonical dynamic universe artifact (`cache/research/research_universe_build_latest.json`) + metrics computed from `cache/prices/*.parquet` (`_seed_rows_from_price_cache`, same UNIVERSE_DEFINITION floors, same rank formula, session-aware `bars_stale`). Resemblance labels only when the snapshot is session-fresh; otherwise `UNAVAILABLE_STALE_SOURCE` with `source_as_of` + `source_age_sessions` in `seed_source_info`. No usable source ⇒ board fails closed with an explicit error. |
| `research/stock_lens_runner.py` (Market Posture layer) | Rebuilt `build_research_bte` against the snapshot | **GATED**: stale source ⇒ posture layer state `UNAVAILABLE_STALE_SOURCE` with `source_as_of` / `source_age_sessions` — never an apparently-current posture. |
| `research/regime_forecast.py` | Optional universe-breadth feature (% of `strategy_candidates` above MA20/50/200) | **GATED**: stale source ⇒ loader returns None and breadth degrades to its designed `available=False` path (logged with source age). |
| `research/prebuild_stock_lenses.py` | "Liquid top" coverage tier ranked by snapshot `avg_dollar_volume_20` | **GATED**: stale source ⇒ tier skipped visibly (logged with source age). Follow-up option: migrate to parquet-computed dollar volume. |

## Passive / correct-by-design consumers (no change)

| Consumer | Why unchanged |
|---|---|
| `core/evidence_freshness.py` | Freshness *reporter* — its purpose is to expose the snapshot's age (dashboard Mode 3). |
| `core/universe.py` | The (decommissioned) writer, not a consumer. |

## Archived research tooling (not in any scheduled chain — documented only)

These one-off 1G-era audit tools read the snapshot as a *historical* input;
they are not scheduled and running them today reproduces point-in-time
studies. Do **not** treat their output as current without checking dates.

- `research/universe_dynamic_selection.py` (1G.7B)
- `research/scanner_cap_audit.py` (1G.8)
- `research/sniper_starvation_audit.py` (1G.17)
- `research/strategy_lab_data.py` (1H.1)

## Artifact retention

The frozen snapshot file is **retained** (do not delete): the archived tools
above still reference it, and it documents the decommission-era universe.
It must never regain live consumers without a session-freshness gate.
