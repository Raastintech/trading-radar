# RS/Theme → Lens/Gatekeeper Triage — Phase 1G.9

*Generated 2026-07-08T00:41:30.758188+00:00 · research-only · cache-only. Routing labels only — not buy/sell signals, not paper signals, not trade proposals. Does NOT modify the production universe, strategy gates, execution, or governance.*

**Verdict:** `NEED_MORE_DATA`

## Why this surface exists
Phase 1G.8 found 333/356 proposed-dynamic early leaders are killed by the Voyager/Sniper structural gates, but most rejections are cache-depth artifacts. Its own recommendation was to route RS/theme early leaders to the Stock Lens/Gatekeeper as a research-only second surface that BYPASSES those score gates. This report is that surface — diagnostic only, no gate change.

## Triage quality summary (Task 4)

| metric | value |
|---|--:|
| candidates evaluated | 30 |
| needs Lens | 23 |
| needs Gatekeeper | 0 |
| Lens-ready (both artifacts fresh) | 0 |
| too extended | 1 |
| blocked | 1 |
| research-watch | 0 |
| low-quality noise | 3 |
| not enough data | 2 |
| with options confirmation | 9 |
| in leading themes | 15 |
| killed only by Alpha-board cap | 2 |
| killed by cache/gate artifact | 6 |

**Key question:** Would routing RS/theme leaders to Lens/Gatekeeper reveal useful candidates, or just create noise?

## Gate rejection decomposition (Task 3)

- Killed by both Voyager+Sniper gates: **27** / 30 evaluable.
- Root causes: `{'cache_or_data_depth_artifact': 0, 'gate_design_mismatch': 6, 'real_quality_rejection': 21, 'unknown': 0}`
- Possibly-valid early candidates (cache-depth + gate-design only): **6**
- Bucketed reasons: `{'no_atr_contraction': 23, 'too_extended': 21, 'volume_insufficient': 16, 'no_breakout': 16, 'insufficient_history_260': 6, 'unknown': 4}`

*cache_or_data_depth_artifact = killed only by the 260/75-bar history gate (shallow cache, not a structure failure); gate_design_mismatch = killed only by breakout/contraction/volume gates an EARLY leader is not meant to satisfy yet; real_quality_rejection = killed by a genuine structural reason (too extended, below MA200 floor). possibly_valid_early_candidates sums the first two — names a Lens/Gatekeeper second surface could legitimately surface.*

## Candidates

| ticker | source | stage | ELS | theme | ext | lens | gk | options | alpha-board | gate root | triage |
|---|---|---|--:|---|---|---|---|---|---|---|---|
| ASTS | overlap | PULLBACK_RECLAIM | 61.1 | space_aerospace | near_ema20 | Neutral | BLOCK | ok | alpha_board_cap | real_quality | **NEEDS_LENS** |
| VPG | overlap | PULLBACK_RECLAIM | 59.8 | hardware | near_ema20 | — | — | — | alpha_board_cap | real_quality | **NEEDS_LENS** |
| ATOM | overlap | PULLBACK_RECLAIM | 56.5 | semiconductors | near_ema20 | — | — | — | alpha_board_cap | real_quality | **NEEDS_LENS** |
| SATL | overlap | PULLBACK_RECLAIM | 56.5 | hardware | near_ema20 | Bearish but oversold | BLOCK | unusable | alpha_board_cap | real_quality | **NEEDS_LENS** |
| RKLB | overlap | PULLBACK_RECLAIM | 55.9 | space_aerospace | near_ema20 | Neutral | BLOCK | poor | alpha_board_cap | real_quality | **NEEDS_LENS** |
| AMBQ | overlap | PULLBACK_RECLAIM | 55.8 | semiconductors | near_ema20 | — | — | — | alpha_board_cap | gate_design_mismatch | **NEEDS_LENS** |
| VSH | overlap | PULLBACK_RECLAIM | 54.4 | semiconductors | near_ema20 | — | — | — | alpha_board_cap | real_quality | **NEEDS_LENS** |
| VOYG | overlap | BROKEN | 54.0 | space_aerospace | near_ema20 | Neutral | BLOCK | poor | alpha_board_cap | passes_a_gate | **LOW_QUALITY_NOISE** |
| OPTX | overlap | PULLBACK_RECLAIM | 52.9 | hardware | near_ema20 | Neutral | BLOCK | — | alpha_board_cap | real_quality | **NEEDS_LENS** |
| ALAB | overlap | PULLBACK_RECLAIM | 46.4 | semiconductors | near_ema20 | — | — | — | alpha_board_cap | real_quality | **NEEDS_LENS** |
| OUST | overlap | PULLBACK_RECLAIM | 45.9 | semiconductors | near_ema20 | — | — | — | alpha_board_cap | real_quality | **NEEDS_LENS** |
| WOLF | overlap | PULLBACK_RECLAIM | 43.7 | semiconductors | near_ema20 | — | — | — | alpha_board_cap | real_quality | **NEEDS_LENS** |
| ENPH | overlap | PULLBACK_RECLAIM | 42.1 | other | near_ema20 | — | — | — | alpha_board_cap | real_quality | **NEEDS_LENS** |
| NVTS | overlap | PULLBACK_RECLAIM | 41.4 | semiconductors | near_ema20 | — | — | — | alpha_board_cap | real_quality | **NEEDS_LENS** |
| FLY | overlap | BROKEN | 38.0 | space_aerospace | constructive | Neutral | BLOCK | unusable | alpha_board_cap | gate_design_mismatch | **BLOCKED** |
| VELO | overlap | LATE_EXTENDED | 27.0 | other | extended | — | — | — | below_mcap_floor_300M | real_quality | **TOO_EXTENDED** |
| LUNR | proposed_dynamic | PULLBACK_RECLAIM | 71.4 | space_aerospace | near_ema20 | — | — | — | alpha_board_cap | real_quality | **NEEDS_LENS** |
| RAL | proposed_dynamic | PULLBACK_RECLAIM | 68.2 | space_aerospace | near_ema20 | Bullish but not buyable yet | BLOCK | unusable | alpha_board_cap | gate_design_mismatch | **NEEDS_LENS** |
| PACS | proposed_dynamic | EMERGING_MOMENTUM | 66.7 | other | — | — | — | — | alpha_board_cap | gate_design_mismatch | **NOT_ENOUGH_DATA** |
| DRS | proposed_dynamic | EMERGING_MOMENTUM | 66.2 | space_aerospace | — | — | — | — | alpha_board_cap | gate_design_mismatch | **NOT_ENOUGH_DATA** |
| NTAP | theme | PULLBACK_RECLAIM | 66.1 | hardware | near_ema20 | — | — | — | alpha_board_cap | real_quality | **NEEDS_LENS** |
| RGTI | theme | PULLBACK_RECLAIM | 65.5 | hardware | near_ema20 | Avoid / no edge | BLOCK | unusable | alpha_board_cap | real_quality | **NEEDS_LENS** |
| COHR | proposed_dynamic | PULLBACK_RECLAIM | 65.5 | hardware | near_ema20 | Neutral | BLOCK | unusable | alpha_board_cap | real_quality | **LOW_QUALITY_NOISE** |
| LPTH | proposed_dynamic | PULLBACK_RECLAIM | 64.3 | hardware | near_ema20 | — | — | — | alpha_board_cap | passes_a_gate | **NEEDS_LENS** |
| VIK | proposed_dynamic | PULLBACK_RECLAIM | 61.4 | other | near_ema20 | — | — | — | alpha_board_cap | real_quality | **NEEDS_LENS** |
| JHX | proposed_dynamic | PULLBACK_RECLAIM | 60.2 | other | near_ema20 | — | — | — | alpha_board_cap | real_quality | **NEEDS_LENS** |
| CAH | proposed_dynamic | BREAKOUT_CONFIRMED | 59.5 | biotech_healthcare | near_ema20 | — | — | — | alpha_board_cap | gate_design_mismatch | **NEEDS_LENS** |
| APLS | proposed_dynamic | LOW_QUALITY_NOISE | 58.9 | biotech_healthcare | near_ema20 | — | — | — | alpha_board_cap | real_quality | **LOW_QUALITY_NOISE** |
| ANET | proposed_dynamic | BREAKOUT_CONFIRMED | 58.7 | hardware | constructive | Bullish but not buyable yet | WATCH | unusable | above_mcap_ceiling_80B | passes_a_gate | **NEEDS_LENS** |
| SMTC | RS | PULLBACK_RECLAIM | 58.4 | semiconductors | near_ema20 | — | — | — | alpha_board_cap | real_quality | **NEEDS_LENS** |

## Targeted refresh plan (Task 2 — design only, not executed)

DESIGN ONLY. No refresh is executed by this report. Run the commands below only with explicit operator approval.

- **build/refresh Stock Lens (PROVIDER calls — operator approval required)** — ~29 stock-lens builds (Alpaca bars + FMP profile/options per ticker)
  ```
  ./scripts/run_research_cycle.sh lens VPG ATOM AMBQ VSH ALAB OUST WOLF ENPH NVTS VELO LUNR PACS DRS NTAP LPTH VIK JHX CAH APLS SMTC ASTS SATL RKLB VOYG OPTX FLY RAL RGTI ANET
  ```
- **refresh Executive Gatekeeper (cache-first; FMP earnings calendar only)** — ~9 gatekeeper rebuilds (cache-first, no per-ticker provider fan-out)
  ```
  ./scripts/run_research_cycle.sh gatekeeper-refresh --watch ASTS SATL RKLB VOYG OPTX RAL RGTI COHR ANET
  ```

## Forward maturation

Each run appends today's triage to `data/research/rs_theme_lens_triage_history.jsonl` (idempotent per date/ticker). Forward outcomes will later answer whether research-watch names outperform, too-extended names pull back, the Lens/Gatekeeper rejected correctly, and whether RS/theme triage beats the Alpha board. No future data is stored today.

