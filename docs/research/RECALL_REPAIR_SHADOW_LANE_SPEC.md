# Recall-Repair Shadow Lane — Spec (Path B)

**Status:** research-only / cache-only · **Created:** 2026-06-07 ·
**Modules:** `research/recall_repair_shadow_lane.py`, `research/recall_repair_shadow_forward.py`
**Origin:** June Dashboard Truth Audit (`docs/research/DASHBOARD_TRUTH_AUDIT.md`) —
core bottleneck = `UNIVERSE_MISS` + low recall (funnel ~1.1% vs an 18% sector_rs baseline).

## Purpose

Test, with forward evidence, whether **simple** sector-relative strength +
20/60 momentum on a deep, theme-aware universe catches forward winners *earlier*
and with *better precision* than the current scanner funnel — and produce the
**dated, point-in-time historized board** the audit found was missing (forward
precision was previously `NOT_COMPUTABLE_YET` because only a latest snapshot
existed).

## Hard guardrails (non-negotiable)

- No paper signals, no trade proposals, no routing to execution.
- No provider calls; cache-only reads of `cache/prices*`, `cache_meta`
  (read-only), and existing research sidecars.
- No DB writes/mutations; no governance / execution / live-capital / Gatekeeper
  / production-universe / strategy-gate changes.
- History is **append-only** and **idempotent** per `(asof_date, ticker, version)`.
- The dashboard remains cache-only; it reads the forward verdict, never runs the lane.

## Inputs

| Source | Use |
|---|---|
| `cache/prices_deep/*` (preferred) + `cache/prices/*` | OHLCV; `dataio.load_prices` prefers deep |
| `cache_meta fmp:profile:*` | sector + theme (`dataio.classify_theme`) |
| SPY/QQQ parquet | benchmark calendar + relative returns |
| `scanner_funnel_trace_latest.json` | `scanner_saw` cross-ref (degrades to `unknown`) |
| `alpha_discovery_board/overlay_latest.json` | `on_alpha_board` cross-ref |
| `stock_lens_{T}` / `executive_gatekeeper_{T}` sidecars | `has_lens` / `has_gatekeeper` |
| `scanner_truth_summary_latest.json` | funnel-recall benchmark (the number to beat) |

## Candidate logic (deliberately simple, documented thresholds)

Per liquid ticker at the as-of bar (filters: price $5–$1000, avg vol ≥300k,
avg $vol ≥$5M, ≥65 bars for r60):

- **sector_rs** = 20d return − sector-median 20d return (flag ≥ +0.10)
- **mom_20_60** = 20d ≥ +10% AND 60d ≥ +20%
- **theme leadership** = ticker's profile theme is a top-3 theme by median 20d
  return AND the ticker beats its theme median (excludes `other`/`unknown`)
- **liquidity** = avg 20d $vol (floor + 0-1 score)
- **volume expansion** = 20d avg vol ≥ 1.3× prior 20d
- **extension** = price vs MA50 / 20d run → `NORMAL` / `EXTENDED` / `PARABOLIC`
  (used to **mark**, never to silently discard)
- **bar-depth** = `DEEP` (≥200 deep bars) / `SHALLOW` / `INSUFFICIENT`

### Labels (research routing only)

`SHADOW_RS_LEADER` · `SHADOW_MOMENTUM_LEADER` · `SHADOW_THEME_LEADER` ·
`SHADOW_PULLBACK_WATCH` · `SHADOW_LATE_EXTENDED` · `SHADOW_NO_EDGE`

Parabolic + leadership → `SHADOW_LATE_EXTENDED` (marked, kept, historized — **not
auto-traded**). Ranking dampens EXTENDED (×0.6) and PARABOLIC (×0.15) so the top
of the board is *early* leaders, while late names still accrue forward evidence.

## Outputs

- `cache/research/recall_repair_shadow_lane_latest.json` — today's ranked board (cap 150).
- `logs/recall_repair_shadow_lane_latest.txt` — human report.
- `data/research/recall_repair_shadow_lane_history.jsonl` — **dated, append-only,
  idempotent** history (per-candidate: ticker, asof_date, rank, label, sector_rs,
  r20, r60, mom score, theme score/leader, liquidity, extension, ext_pct,
  vol_ratio, bar_depth, reason_codes, price_at_asof, on_alpha_board, has_lens,
  has_gatekeeper, scanner_saw, version, source_versions).

## Forward validation (`recall_repair_shadow_forward.py`)

Per distinct as-of date, recomputes the liquid universe forward outcomes
(point-in-time; features ≤ as-of, outcomes > as-of) and measures, pooled across
dates and at horizons **1/3/5/10/20d**:

- forward return, rel-SPY, rel-QQQ, MFE, MAE, win rate;
- **recall** before +20/+30/+50% (winner = forward MFE ≥ thr over 20d) vs the
  funnel benchmark, plus **precision** and **false-positive** burden;
- top-decile (by rank) hit rate;
- controls: **random liquid** (seeded → reproducible), **sector_rs top**,
  **Alpha board** (current-annotation proxy — caveated), **SPY/QQQ**, **cash**;
- theme-earliness: leading-theme winners caught by shadow vs Alpha.

**Caveat (documented):** forward *returns* are as-of correct; the cross-ref
*annotations* (alpha/lens/gatekeeper/scanner) use current artifacts, so the "vs
Alpha" line is best-effort context, not as-of truth.

## Decision gates (Task C) → verdict

A gated verdict, never overclaiming from a small sample:

| Gate | Floor |
|---|---|
| history days (distinct as-of) | ≥ 10 |
| mature ticker-days (primary 5d) | ≥ 300 |
| beats random control | rel-SPY at 5d and/or 10d |
| recall improvement | shadow recall@+20% ≥ 2× funnel (≈ 2×1.1%) |
| false positives | not exploded (≤ ~92%) |
| theme earliness | leading-theme winners > Alpha |

Verdicts: `NEED_MORE_DATA` → `NO_VALUE` → `PROMISING_BUT_UNPROVEN` →
`RECALL_EDGE_DETECTED` → `READY_TO_FEED_LENS_RESEARCH_ONLY`.
**No production routing at any verdict** — the terminal state only authorises
feeding candidates to **Lens/Gatekeeper research**.

## Run

```bash
./scripts/run_research_cycle.sh recall-shadow            # build + historize today's board
./scripts/run_research_cycle.sh recall-shadow-forward    # refresh forward verdict
# both also run as a cache-only tail of `run_research_cycle.sh nightly`
```

Backfill point-in-time boards (bootstraps forward evidence; no look-ahead):
`... recall-shadow --asof-offset N`.

## Current status

See `docs/research/RECALL_REPAIR_SHADOW_FORWARD_RESULTS.md`. First run
(7 backfilled as-of dates) is **encouraging but UNPROVEN**: shadow beats random
at every horizon and recall@+20% ≈ 16% vs the funnel's 1.1% — but the verdict is
`NEED_MORE_DATA` (history_days = 7 < 10). Nightly cadence grows it toward the gate.
