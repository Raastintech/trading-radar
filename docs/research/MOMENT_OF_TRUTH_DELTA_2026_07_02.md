# Moment of Truth — Delta Report vs 2026-06-27

*Generated: 2026-07-02 | Mode: RESEARCH_ONLY | Delta against `MOMENT_OF_TRUTH_2026_06_27.md` only — not a full re-audit.*

---

## Executive Delta

**The single biggest change since June 27 was discovered today and is not in any prior report: the scan-universe price cache had been frozen since 2026-06-12.** 974/1001 universe tickers had no bars newer than June 11–12 while SPY stayed current. Every RS/momentum figure, every scanner selection, and every unresolved forward return between June 15 and July 1 was computed against stale (and in 9 cases corrupt) price series. This was repaired today (2026-07-02): full universe refreshed from FMP (974/974 ok), scan lanes now read merged deep+shallow history, and stale-price (7d) + corrupt-feed (>2.5x/<0.4x one-day print) guards now quarantine bad series at load time.

Direct consequence: the forward tracker, which had been starved of resolution bars, matured **464 entries at 5d and 134 at 10d in a single pass today** (was 75/2 at last night's nightly, 14/0 on June 27). The evidence dam broke — but it describes the *stale-selection era* cohort, which caps what we can conclude from it (see §8).

---

## 1. What changed since the June 27 run?

| Metric | Jun 27 | Jul 2 (latest) | Delta |
|---|---|---|---|
| Forward tracker total entries | 746 | 1,146 | +400 |
| Matured 5d | 14 | **464** | +450 |
| Matured 10d | 0 | **134** | +134 |
| Sample status (overall) | TOO_EARLY | ROBUST | ↑↑ |
| Overall verdict field | NEED_MORE_DATA | MIXED | changed |
| Radar candidates | 78 | 104 | +26 |
| Scanner recall | 2.0% vs 19.1% baseline | 1.8% vs 34.4% baseline | baseline ↑, recall flat |
| Recall bottleneck classification | UNIVERSE_MISS | FILTER_TOO_STRICT | reclassified |
| Data quarantine | 16 (all INSUFFICIENT_HISTORY) | nightly: 22 (21 IH + 1 DQ); radar: 7 (4 IH + 3 DQ) | no longer perfectly homogeneous |
| Market regime | — | DEFENSIVE_ROTATION / UPTREND_MILD | context |

Additional structural changes shipped today (2026-07-02), after the June 27 report:
- `research/refresh_universe_prices.py` + `refresh-universe-prices` runner cmd; wired into nightly before the scanner.
- Premarket now runs the full Phase 4A scanner chain (Mode 4 staleness ~24h → ~12h).
- Scanner sidecar gained `stale_price_skipped_count` (64 today) and `data_suspect_skipped_count` (9 today: ASRT, ASTC, INHD, PIII, REPL, SKLZ, STI, SUNE, XOS — corrupt FMP feeds that were polluting the RS board).
- Mode 4 dashboard redesigned (top-10 lanes, multi-category hits panel, data-quality header strip) — uncommitted at time of writing.

## 2. Did the resolved=false tracking gap fix work?

**Yes, for reporting; the underlying flag debt remains.** The fix (commit e17d70e) made the tracker surface `matured_5d_entries` by counting populated `ret_5d` instead of trusting the `resolved` flag. The operator summary went from "Matured: 0" (misleading) to correct counts. However, the JSONL itself still carries `resolved=false` on all 464 entries with populated returns — the flag was never backfilled. Cosmetic now, but any future consumer that trusts `resolved` will repeat the June 27 confusion. Recommend a one-line backfill or deprecating the field.

## 3. Did the nightly docs correctly show Matured 5d: 14 and Matured 10d: 0?

**Yes.** Git history of `NIGHTLY_OPERATOR_SUMMARY.md` shows the June 27 second-run nightly printed exactly `Matured 5d: 14 | Matured 10d: 0` (replacing the prior `Matured: 0`), and last night's (July 2, 00:33 UTC) printed `1000 entries | Matured 5d: 75 | Matured 10d: 2`. The doc trail is consistent with the fix working from the day it shipped.

## 4. Did high-priority / watchlist / extended labels change?

Yes, substantially — expected, since today's board is the first computed on fresh prices:
- **HIGH_PRIORITY_RESEARCH: HOOD → SDGR** (1 slot, unchanged count). HOOD is now WATCHLIST_RESEARCH with extension STRETCHED — consistent with its run.
- **RESET_WATCH: NUE, TECH → FCEL, EVH, CUE, PENG, UMC, AGYS** (2 → 6; all PARABOLIC/EXTENDED on fresh data).
- **WATCHLIST_RESEARCH: 17 → 21** (new: KSS, ALK, RDDT, CNNE, NTNX, FUN, ACVA, XRX, KULR, RYAN, CBZ, LEVI, SFM, DUOL, …).
- Change detector on today's run: 7 new / 8 dropped — the drops include **ASRT, INHD, PIII, SKLZ** (corrupt-feed names removed by the new sanity guard) plus NVO, CBRL, RHI, VCIG. The guard visibly cleaned the radar on day one.

## 5. Ranked fill, quarantine, sector coverage, benchmark readiness still healthy?

- **Ranked fill: healthy at the runs that matter, but a NEW test-isolation bug found today.** Last night's nightly and today's 16:45/17:01 intraday runs built a full 1,000-ticker universe (fallback=False). However, at 17:54 UTC the unit-test suite **clobbered the production `research_universe_build_latest.json`** with a 2-ticker fixture universe (tests patch `PRICE_DIR` but not the output sidecar paths). Restored by re-running the scanner. This matters more now than before: `refresh_universe_prices.py` reads this sidecar for its ticker list — a clobbered file would silently degrade the nightly price refresh to SPY-only. **Must fix: patch `UNIVERSE_BUILD_JSON`/`UNIVERSE_MISS_JSON`/log paths in the test fixtures.**
- **Quarantine:** count improved (16 → 7 on the radar) but composition is no longer perfectly homogeneous (4 INSUFFICIENT_HISTORY + 3 DATA_QUARANTINE). Small numbers; watch, don't act.
- **Sector coverage:** scanner 104/104 (100%) — held. Forward-tracker sector-ETF attribution 977/1,146 (85.2%, was 84.7%) — held.
- **Benchmark readiness:** SPY/QQQ loaded; 85/134 matured-10d entries have SPY/QQQ baselines (63%); sector-ETF 10d baselines on 54. Coverage is real but partial — benchmark-relative claims rest on the 85-entry subset.

## 6. Did new 5d evidence appear?

**Yes — a flood, all unlocked today.** Per-label (5d-matured n / verdict):

| Label | n (5d) | Status | Verdict | Notable |
|---|---|---|---|---|
| EARLY_ACCUMULATION | 136 | MEANINGFUL | MIXED | +2.13% vs SPY avg, but 10d win-rate 42% |
| SECTOR_LEADER | 69 | PROVISIONAL | **NO_FORWARD_EDGE** | 10d mean −3.3%, win-rate 34.5% |
| ASYMMETRIC_RECOVERY_WATCH | 68 | PROVISIONAL | **NO_FORWARD_EDGE** | 10d mean −1.49% |
| CATALYST | 50 | TOO_EARLY (10d) | NEED_MORE_DATA | |
| BEATEN_DOWN | 49 | PROVISIONAL | MIXED | 10d win-rate 57%, +0.55% — best 10d cohort |
| SPECULATIVE_10X | 21 | PROVISIONAL | MIXED | +2.56% vs SPY |
| RS_MOMENTUM_LEADER | 0 | TOO_EARLY | NEED_MORE_DATA | lane too new (58 entries, none matured) |

Note the reversal: June 27's tiny sample crowned SECTOR_LEADER (+3.50%, n=2); at n=69 it's the *worst* cohort. A textbook illustration of why the 30-entry floor existed.

## 7. Is there still 0 matured 10d evidence?

**No — 134 entries now have 10d returns** (85 with SPY/QQQ baselines). Overall 10d read: win-rate 41.8%, mean −0.94% absolute, +1.34% mean vs SPY but **median −1.4% vs SPY** (a few big winners carry the mean).

## 8. Is the correct verdict still NEED_MORE_DATA?

**Yes — but for a new reason.** The sidecar now says MIXED/ROBUST, and on its face that's fair for the June 15–July 1 cohort: no label shows a convincing edge, two show none at all. But that cohort was **selected by a scanner running on 3-week-stale prices with corrupt feeds on the board** — it measures the stale-selection-era engine, not the one running now. Today (2026-07-02) is effectively day zero for the repaired engine: fresh prices, sanity guards, MA200 depth, twice-daily cadence. Two things are simultaneously true:
1. The old cohort's evidence (MIXED, no forward edge in sector-leader/asymmetric lanes) argues **against** shipping any scoring change based on past boards.
2. The repaired engine has **zero** matured forward evidence of its own.

Both point the same way: **NEED_MORE_DATA remains the operative verdict.** Recommend tagging tracker entries with a cohort marker (`pre_fresh_fix` / `post_fresh_fix`, boundary 2026-07-02) so the two eras are never pooled.

## 9. Are Phase 4B and scoring changes still blocked?

**Yes.** The 1G.8-style promotion gates (≥30 benchmarked matured entries *for the engine as currently configured*, recall/FP/theme/sector criteria) are unmet for the post-fix cohort. The pre-fix cohort's MIXED/NO_FORWARD_EDGE read gives no affirmative case either. Do not tune thresholds, labels, or scores; do not start Phase 4B.

## 10. What to monitor over the next 7 days (Jul 2 → Jul 9)

| # | Watch | Where | Healthy looks like |
|---|---|---|---|
| 1 | Universe price refresh runs nightly + premarket | `logs/universe_price_refresh_latest.txt` | refreshed_ok > 0, refresh_failed = 0, already_fresh growing toward ~1000 |
| 2 | Stale/bad-feed counters | Mode 4 header strip / scanner sidecar | stale-px skipped falling toward ~0; bad-feed skipped stable single digits |
| 3 | Post-fix cohort maturation | forward tracker (`research_forward_latest.json`) | first post-Jul-2 5d returns land ~Jul 9-10; RS_MOMENTUM_LEADER lane starts maturing |
| 4 | Benchmark coverage on matured entries | `benchmark_readiness` block | entries_with_spy_10d rising with matured count (63% → 90%+) |
| 5 | Scanner recall vs baseline on fresh data | nightly summary | recall should move off 1.8% now that RS values are real; if it doesn't, FILTER_TOO_STRICT diagnosis is confirmed |
| 6 | Quarantine composition | daily alpha radar | stays small; DATA_QUARANTINE (non-IH) portion doesn't grow |
| 7 | Test-clobber fix | `tests/unit/test_research_scanner_universe.py` | output paths patched; production sidecar never again written by pytest |
| 8 | resolved-flag backfill or deprecation | tracker JSONL | one decision made, applied once |
| 9 | No tuning | everywhere | zero scoring/threshold/label changes this week |

---

*RESEARCH_ONLY — Not a signal. Not a recommendation. No live capital. Human review required before any action.*
