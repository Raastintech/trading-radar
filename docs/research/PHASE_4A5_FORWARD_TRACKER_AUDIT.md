# Phase 4A.5 — T5: Forward Tracker Audit

*Generated 2026-06-16 | Adversarial audit | RESEARCH_ONLY*

---

## 1. Observation Logging

**File:** `research/research_watchlist_forward_tracker.py`
**History:** `data/research/research_watchlist_history.jsonl`
**Key format:** `"{ticker}|{appearance_date}"`

| Property | Implementation | Correct? |
|----------|---------------|----------|
| One observation per ticker per calendar date | Dict keyed `ticker\|date`; duplicate keys are overwritten (last-write-wins on same day) | ✅ Idempotent same-day runs |
| Repeated appearances (ticker reappears next day) | New key `ticker\|new_date` → new row | ✅ Each day is an independent observation |
| No duplicate same-day rows | Dict key guarantees uniqueness per ticker+date | ✅ |
| Labels and scores frozen at observation time | Written at first insertion; never overwritten after initial entry for that key | ✅ |
| Later label changes do not rewrite history | Dict insert uses `if key not in history` guard | ✅ |

**JSONL rewrite:** The tracker rewrites the full JSONL on every run:
```python
lines = [json.dumps(rec, separators=(",", ":")) for rec in history.values()]
HISTORY_JSONL.write_text("\n".join(lines) + "\n" if lines else "", encoding="utf-8")
```
This correctly reflects updated `ret_*d` fields and `resolved` status. Labels and scores in each record are not touched after initial logging. ✅

⚠ **YELLOW FLAG — Scalability:** Full rewrite is O(n) in history size. At 118 entries (day 1) this is negligible. Past ~10,000 entries (∼300 unique tickers × 33 trading days), the rewrite could add noticeable latency to the nightly cycle. Recommend batched append-and-rewrite strategy when history grows past 5,000 entries.

---

## 2. Return Calculation

**Function:** `_forward_return(closes_with_dates, appearance_date, horizon)`

```python
# Find entry: first bar AT OR AFTER appearance_date
idx = first i where date >= appearance_date

# Guard: need horizon bars after entry
if idx + horizon >= len(closes_with_dates):
    return None   # future not yet available

# Compute: %change from entry close to exit close
entry_close = closes_with_dates[idx][1]
exit_close  = closes_with_dates[idx + horizon][1]
return (exit_close / entry_close - 1.0) * 100.0
```

| Property | Correct? |
|----------|----------|
| Uses future prices only (after appearance_date) | ✅ Entry found at or after date; no look-back |
| 5d/10d/20d horizons use trading-day bars (not calendar days) | ✅ Parquet bars are trading days; `horizon` = number of bars |
| Guard when insufficient future bars | ✅ Returns None; stays unresolved |
| No SPY/QQQ/sector comparison yet | ⚠ WARN — absolute return only; no alpha vs baseline |

**Weekend / holiday handling:** If `appearance_date` is a non-trading day (e.g., observation date is a Sunday), the entry is set to the next trading day's close via `d >= appearance_date`. This is correct behavior — first available trading price after observation. ✅

**Return labeling:** `ret_5d`, `ret_10d`, `ret_20d` are % returns. `ret_10d` is the primary verdict metric. Win rate is `(ret_10d > 0)` — this is a binary outcome measure, not risk-adjusted. ⚠ **YELLOW FLAG — win rate treats +0.01% and +20% identically; mean_ret_10d is also reported but the verdict thresholds are win-rate based.**

---

## 3. Sample Discipline

| Status | Threshold (code) | Threshold (docstring) | Match? |
|--------|-----------------|----------------------|--------|
| TOO_EARLY | n_matured < 10 | "fewer than 5 matured" (module header) | ❌ **MISMATCH** |
| PROVISIONAL | 10 ≤ n < 30 | — | — |
| MEANINGFUL | 30 ≤ n < 100 | — | — |
| ROBUST | n ≥ 100 | — | — |

**BUG:** `MIN_MATURED_FOR_VERDICT = 5` is defined at line 76 but **never used**. The actual threshold used in `_compute_verdicts` is `SAMPLE_THRESHOLD_PROVISIONAL = 10`. The module header docstring says "NEED_MORE_DATA — fewer than 5 matured entries" but the code requires 10.

⚠ **YELLOW FLAG — stale constant + stale docstring.** The constant `MIN_MATURED_FOR_VERDICT = 5` should either be removed (replaced by `SAMPLE_THRESHOLD_PROVISIONAL`) or updated to 10. The module header docstring must be corrected.

**Current state:** 118 total entries, 0 matured (all `ret_10d = None`). This is correct — collection started 2026-06-15; 10d returns cannot be available until 2026-07-01 at the earliest. **NEED_MORE_DATA is the correct verdict.**

**Verdict gate:** No bucket will produce a performance claim before 10 matured observations. No bucket can claim PROMISING before 30 matured with ≥ 70% positive rate AND mean_10d > 2%. This is appropriately strict. ✅

---

## 4. Evidence Usefulness

**Baselines currently computed:** NONE.

The forward tracker tracks absolute % return (ret_10d, ret_5d, ret_20d) and win rate. It does **not** compare to:
- SPY return over the same horizon
- QQQ return over the same horizon
- Sector ETF return over the same horizon
- Simple RS-top universe baseline

⚠ **YELLOW FLAG — No baseline comparison.** Without a baseline, a "PROMISING" verdict (70% win rate, mean +2%) tells you nothing if SPY also returned +2% over the same period. The tracker will produce EARLY_SIGNAL / PROMISING labels that are not yet alpha-adjusted.

**Recommendation before trusting bucket verdicts:** Add SPY and QQQ returns at the same horizons to every observation row. Compute `ret_10d_vs_spy` and `ret_10d_vs_qqq` as the primary alpha measure. Gate bucket PROMISING on `mean_10d_vs_spy > 0` in addition to current thresholds.

---

## Summary Verdict

| Property | Verdict |
|----------|---------|
| Observation logging integrity | ✅ PASS — idempotent, frozen labels, no label backdating |
| Return calculation (no look-ahead) | ✅ PASS — future prices only after maturity; guarded correctly |
| Sample discipline (NEED_MORE_DATA gate) | ✅ PASS (code is strict at 10 matured) ⚠ WARN (docstring says 5, constant 5 unused) |
| Baseline comparison | ⚠ WARN — absolute returns only; no SPY/QQQ/sector alpha baseline |
| JSONL scalability | ⚠ WARN — full rewrite; acceptable now, needs attention past 5k entries |

**Forward tracker structural integrity: PASS.** Evidence cannot be polluted by future leakage or rewritten labels. The missing baseline is the most important gap to address before the first bucket reaches PROVISIONAL status.
