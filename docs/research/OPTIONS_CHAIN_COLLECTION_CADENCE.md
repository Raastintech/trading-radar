# Options Chain Collection Cadence (Phase 1J.1)

Status: **DATA_COLLECTION_ONLY**. No strategy usage until the minimum history gates below
are met, and even then only through a new pre-registered feasibility phase.

## Recommended cadence

- **Once daily, late regular session or after close** (e.g. 15:45–16:30 ET or with the
  existing post-close research window). One run captures the chain state for the day.
- Suggested invocation (manual or future timer — no timer is created by Phase 1J.1):

  ```bash
  SNIPER_ENV_PATH=/home/gem/secure/trading.env .venv/bin/python \
    research/options_chain_snapshot_collector.py
  ```

- The run is idempotent per (symbol, date): re-running the same day skips already-written
  symbols, so a retry after a partial failure is safe and cheap.
- **Optional second midday snapshot**: deferred. Only add it later if provider budget
  clearly allows; one honest daily snapshot is sufficient for 20–70 DTE premium research.
- **No dashboard provider calls** — the dashboard stays cache-only; collection happens only
  via this CLI (or a future timer wrapping it).

## Budget posture

Default guards: ≤20 symbols/run, ≤4 expirations/symbol, ≤150 provider calls/run,
±30% strike band. At defaults a daily run costs ~100–140 provider calls
(spot + expirations + chains per symbol).

## Minimum data gates (pre-registered; no strategy work before these)

| History | Unlocks |
|---|---|
| 60 trading days | IVR-signal research **PARTIAL** (signal-only, no execution modeling) |
| 120 trading days | IVR research **FEASIBLE** |
| 6 months of chains | basic defined-risk spread backtest **PARTIAL** (entry pricing real; regime diversity still thin) |
| 12 months of chains | stronger spread backtest feasibility (covers more vol regimes; still < 1 full market cycle — label accordingly) |

Collection started 2026-06-12. Approximate unlock dates at daily cadence:
60 trading days ≈ early September 2026; 120 ≈ early December 2026; 6 months ≈ mid-December
2026; 12 months ≈ June 2027.

## Doctrine

- Snapshots are point-in-time and append-only; current chains are never back-dated.
- Any future backtest must satisfy `docs/research/OPTIONS_BACKTEST_REALISM_CHECKLIST.md`.
- `OPTIONS_PREMIUM_STRATEGY_STATUS = DATA_COLLECTION_ONLY` until a new feasibility phase
  re-evaluates against the gates above. No strategy lab activation, no paper-shadow, no
  trade proposals.
