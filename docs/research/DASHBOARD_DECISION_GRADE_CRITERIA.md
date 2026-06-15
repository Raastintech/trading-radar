# Dashboard Decision-Grade Criteria

**Date:** 2026-06-07 · **Companion:** `DASHBOARD_TRUTH_AUDIT.md`

Defines what Mode 1 / Mode 2 / Mode 3 must satisfy before the dashboard may be
called **decision-grade** — i.e. an operator can trust it to *not mislead* about
system state or selection edge. Today the dashboard is **operationally
trustworthy but not decision-grade**, because it conflates "system OK" with
"edge exists" and surfaces several stale/benign-stale fields without clear cause.

> Decision-grade ≠ "the system can pick trades." It means: every panel is
> current or visibly labeled stale, and no panel can be misread as implying an
> edge that has not been proven.

## Hard gates (all must pass)

| # | Criterion | Current | Gap / fix |
|---|---|---|---|
| 1 | **No stale MCP audit shown as actionable.** STALE must state *why* (aged / session-changed / forecast-newer) and not say "rerun required" when the cause is a benign `session_changed`. | FAIL (C1) | F1 |
| 2 | **No unknown universe age.** Universe snapshot must show build age + bar-depth; flag when depth < gate requirement (e.g. MA200). | PARTIAL | surface bar depth |
| 3 | **No unknown daily-bars freshness.** Each price-dependent panel shows the underlying parquet age; shallow-cache (<200 bars) is flagged. | FAIL | price-cache depth banner |
| 4 | **No foreign / illiquid earnings-wall pollution.** Earnings wall must never fall back to `_importance==0` rows; filter to US-listed / known-universe symbols. | PARTIAL (fallback `or evts[:5]`) | filter fallback |
| 5 | **Scanner recall above threshold OR clearly labeled WEAK.** A persistent line must show funnel recall vs best simple baseline. | FAIL (C6) | **F5** |
| 6 | **Alpha board has outcome tracking + stale banner.** Overlay older than 24h must show a STALE banner; forward precision status surfaced (currently `NOT_COMPUTABLE_YET`). | FAIL (C3) | F2 + historize |
| 7 | **Gatekeeper precision status visible** with horizon maturity (5d vs 10d/20d n). | PARTIAL | label SHORT-HORIZON UNCONFIRMED |
| 8 | **Power-trend status** shown with `NEED_MORE_DATA` + n-vs-floor. | PARTIAL | label |
| 9 | **RS/theme forward status** shown as `NEED_MORE_DATA`/unstable, never as a "go". Verdict must be reproducible (seeded control, frozen as-of). | FAIL (C8) | F6 |
| 10 | **STANDBY vs "entries permitted" conflict resolved.** Permissive phrasing gated on `session==REGULAR` and `readiness != STANDBY`. | FAIL (C2) | F3 |
| 11 | **No frozen sleeve shown as active/selective.** Frozen sleeves render `FROZEN (date)`, not an active `/N` target. | FAIL (C4) | F4 |
| 12 | **No misleading "system OK" implying edge.** Operational-health green must be visually separate from a selection-edge indicator. | FAIL (C6) | **F5** |
| 13 | **Paper open-count disambiguated.** Legacy-quarantine "open" rows separated from live broker positions. | PARTIAL (C5) | label split |
| 14 | **Unknown symbols labeled.** Shape-valid but unknown tickers (e.g. `QTSLA`) render `UNKNOWN SYMBOL`, not "insufficient bars". | FAIL (C7) | F8 (optional) |

## Soft criteria (recommended)

- Every research verdict panel shows: `verdict · n_matured · primary_horizon ·
  min-n floor · generated_at age`. A verdict below its n-floor is dimmed.
- Operator-invoked-only audits (gatekeeper-precision, power-trend,
  rs-theme-forward) either get a timer or show "operator-refresh; last run N ago".
- A single top-of-dashboard banner: **`SELECTION EDGE: UNPROVEN — funnel recall
  1.1% vs baseline 18%`** until recall work changes it. This is the single most
  important decision-grade addition (F5).

## Acceptance

The dashboard is **decision-grade** when criteria 1–14 pass AND the persistent
selection-edge banner is present. Until then it should be treated as a
**system-health + research-status** console, **not** a trade-selection
authority.
