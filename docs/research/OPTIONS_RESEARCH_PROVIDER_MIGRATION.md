# Options Research Provider Migration — Alpaca → Tradier-Only

**Phase 3B — 2026-06-14**
**Status:** COMPLETE

---

## Summary

Alpaca options data is no longer used. Tradier is the sole options research
provider. Execution via Tradier is permanently disabled.

**Final verdict: CAN OPTIONS RESEARCH CONTINUE WITHOUT ALPACA? YES (when Tradier token is configured)**

---

## Provider Policy (Phase 3B)

| Provider | Role | Status |
|---|---|---|
| Tradier | Options chains, IV, OI, put/call ratio | Research-only (execution permanently disabled) |
| Alpaca options | Snapshot endpoint (no IV/greeks/OI) | REMOVED |
| FMP | No options data | Not applicable |

---

## Modules Updated

| Module | Change |
|---|---|
| `core/options_feed_factory.py` | Phase 3A: Tradier-only; Alpaca options removed |
| `research/stock_research_card.py` | Uses Tradier for options snapshot (IV, OI, put/call ratio) |
| `core/alpha_discovery.py` | Uses `load_options_feed()` → Tradier chain |
| `core/alpaca_options_client.py` | Stub — execution disabled, no network calls |
| `research/tradier_research_health.py` | Phase 3B: token key fixed to TRADIER_API_TOKEN |

---

## Tradier Token Key — Phase 3B Fix

**Root cause of DEGRADED status before Phase 3B:**
`research/tradier_research_health.py` and `research/stock_research_card.py` read
`TRADIER_ACCESS_TOKEN` but `legacy/tradier_options_feed.py` and `core/startup_checks.py`
read `TRADIER_API_TOKEN`.  The env file uses `TRADIER_API_TOKEN`.

**Fix:** Both files now read `TRADIER_API_TOKEN` (matching the feed and startup checks).

---

## What Tradier Provides (Research-Only)

- Options expirations (`/markets/options/expirations`)
- Options chains (`/markets/options/chains`)
- Open interest (OI) per contract
- IV (from `greeks.smv_vol` when available)
- Put/call ratio (derived from chain OI)
- Market clock (`/markets/clock`) — used for health probe

## What Tradier Does NOT Do

- No order placement
- No account mutation
- No paper orders
- No bracket orders
- `TRADIER_EXECUTION_ENABLED = False` in `core/research_mode.py` — enforced at module level

---

## Degraded Behavior

When `TRADIER_API_TOKEN` is not set:
- `research/stock_research_card.py`: options_snapshot → `{"available": False, "reason": "Tradier unavailable or offline mode"}`
- `core/options_feed_factory.py`: `load_options_feed()` returns `None`
- `core/alpha_discovery.py`: `tradier_overlay` is empty; scoring continues without options overlay
- `research/tradier_research_health.py`: reports `DEGRADED` with clear reason

No crash. Research continues with reduced options coverage.

---

## Health Check

```bash
SNIPER_ENV_PATH=/home/gem/secure/trading.env .venv/bin/python research/tradier_research_health.py
# → Tradier research health: OK (when TRADIER_API_TOKEN set)
# → Tradier research health: DEGRADED (when token missing/stub)
```

```bash
./scripts/run_research_cycle.sh tradier-research-health
./scripts/run_research_cycle.sh provider-health   # FMP + Tradier combined
```

---

## Options Chain Collector (Phase 1J.1/1J.2)

The options chain snapshot collector (`core/options_feed_factory.py` + systemd timer
`gem-trader-options-snapshot.timer`) uses the Tradier feed exclusively in Phase 3B.
If Tradier is unavailable, the collector degrades and logs the reason; it does not fail hard.
