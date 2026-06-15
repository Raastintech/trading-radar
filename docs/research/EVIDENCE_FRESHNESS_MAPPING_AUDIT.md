# Mode-3 Evidence Freshness — Source Mapping Audit

**Date:** 2026-06-07 · **Scope:** dashboard-only / cache-only ·
**Artifacts:** `cache/research/evidence_freshness_mapping_audit_latest.json`,
`logs/evidence_freshness_mapping_audit_latest.txt`
**Audit/debug command:** `./scripts/run_research_cycle.sh freshness-audit`

## Symptom

After restarting the dashboard, Mode-3 **Evidence Freshness** still showed:
`daily bars current = unknown`, `universe age = ?m`, `scanner last = 8d08h`.
Restarting changed nothing because the dashboard is cache-only — the panel was
mis-resolving cache metadata, not failing to refresh.

## Root cause

| Field | Old source | Why it broke |
|---|---|---|
| **daily bars current** | derived from `universe_snap.summary.fallback_used` | `_fetch_universe_snap` **discards any snapshot older than 2 h** (`if age_s > 7200: return {}`). Off a fresh weekday build (e.g. on a weekend) the fetcher returns `{}`, so `bars_current` fell to the `else` → **`unknown`** — and it never actually inspected the price cache. |
| **universe age** | `universe_snap._file_age_seconds` | Same 2 h discard → `universe_snap = {}` → `_file_age_seconds = None` → **`?m`** with no age/reason. The snapshot was valid the whole time (built Fri 06-05 19:34, 1000 tickers). |
| **scanner last** | `scan_results.MAX(ts)` (daemon scan loop) | Genuinely **8 d stale** (latest `ts = 2026-05-29`). This is the **legacy daemon scan loop**, not the research pipeline — it was unlabelled, so a stale legacy number read like the whole system was stale even though alpha/scanner-truth/recall-shadow were ~2 h fresh. |

So two of three were a **resolution bug** (a freshness *gate* meant for trade-readiness was being used as the age *source*), and the third was a **mislabelled legacy field**.

## Fix

A new cred-free, cache-only `core/evidence_freshness.py` resolves each field to an
explicit `(status, age, source, reason)` and **never** returns a bare `unknown`
without a reason. The panel now reads dedicated probes instead of the 2 h-gated
`universe_snap` (which is left untouched so trade-readiness keeps its freshness
gating):

- **daily bars** ← `price_cache_bar_status(cache/prices, deep)` — latest SPY bar
  date vs the latest completed trading day → `current / stale Nd / missing /
  unknown:reason`.
- **universe** ← `universe_artifact_meta(universe_snapshot_latest.json)` — mtime/
  `generated_at` age + `base_universe_size` count, **no 2 h discard**; stale only
  when the snapshot's build date is older than the latest completed session.
- **legacy scanner** ← `scan_results.MAX(ts)`, **relabelled** and stale-flagged.
- Added decision-useful fresh rows: **alpha board**, **scanner truth** (with
  recall %), **recall shadow** (age + verdict).

## Source mapping (current)

| Dashboard field | Source artifact | Status | Notes |
|---|---|---|---|
| daily bars | `cache/prices/SPY.parquet` (deep fallback) | **current · latest 2026-06-05** | bar-date vs expected session |
| universe | `cache/universe/universe_snapshot_latest.json` | **current · 1000 tickers** | age from `generated_at`; no 2 h discard |
| alpha board | `cache/research/alpha_discovery_board_latest.json` | present ~2–3 h | premarket producer |
| market forecast | `cache/research/regime_forecast_latest.json` | present ~3 h | premarket producer |
| scanner truth | `cache/research/scanner_truth_summary_latest.json` | present ~3 h · recall 1.1% | research pipeline |
| recall shadow | `cache/research/recall_repair_shadow_forward_latest.json` | present · verdict | Path B forward verdict |
| legacy scanner | `db/trading.db:scan_results.MAX(ts)` | **stale 8d09h** | daemon scan loop, honestly labelled |
| resolver success | `logs/paper_evidence_status.json:last_success_at` | present | paper-evidence resolver |
| scoreboard | `logs/paper_scoreboard_latest.txt` (mtime) | present | |
| paper loop | `logs/paper_evidence_status.json:ok` | OK/UNKNOWN | |

## Before / after

```
BEFORE                                AFTER
daily bars current  unknown           daily bars    current · latest 2026-06-05
universe age        ?m                universe      1d09h · 1000 tickers · snapshot
scanner last        8d08h             alpha board   2h29m    scanner truth 2h41m · recall 1.1%
premarket cycle     fc 2h alpha 2h    recall shadow 25m · NEED_MORE_DATA
resolver success    7h                legacy scanner 8d09h stale
scoreboard refresh  7h                premarket     fc 2h  alpha 2h29m
paper loop          OK                resolver      7h02m    scoreboard 7h02m
                                      paper loop    OK
```

## Guarantees

Cache-only / read-only. No providers, no DB writes (one read-only SELECT), no
execution / governance / live-capital / production-universe / strategy-gate /
Gatekeeper changes; `_fetch_universe_snap` semantics for trade-readiness are
unchanged.
