# RS/Theme → Lens/Gatekeeper Triage — Phase 1G.9

*Generated 2026-06-27T15:59:50.806082+00:00 · research-only · cache-only. Routing labels only — not buy/sell signals, not paper signals, not trade proposals. Does NOT modify the production universe, strategy gates, execution, or governance.*

**Verdict:** `PROMISING_RESEARCH_SURFACE`

## Why this surface exists
Phase 1G.8 found 333/356 proposed-dynamic early leaders are killed by the Voyager/Sniper structural gates, but most rejections are cache-depth artifacts. Its own recommendation was to route RS/theme early leaders to the Stock Lens/Gatekeeper as a research-only second surface that BYPASSES those score gates. This report is that surface — diagnostic only, no gate change.

## Triage quality summary (Task 4)

| metric | value |
|---|--:|
| candidates evaluated | 30 |
| needs Lens | 17 |
| needs Gatekeeper | 1 |
| Lens-ready (both artifacts fresh) | 0 |
| too extended | 5 |
| blocked | 2 |
| research-watch | 0 |
| low-quality noise | 4 |
| not enough data | 1 |
| with options confirmation | 11 |
| in leading themes | 15 |
| killed only by Alpha-board cap | 5 |
| killed by cache/gate artifact | 6 |

**Key question:** Would routing RS/theme leaders to Lens/Gatekeeper reveal useful candidates, or just create noise?

## Gate rejection decomposition (Task 3)

- Killed by both Voyager+Sniper gates: **25** / 30 evaluable.
- Root causes: `{'cache_or_data_depth_artifact': 0, 'gate_design_mismatch': 6, 'real_quality_rejection': 19, 'unknown': 0}`
- Possibly-valid early candidates (cache-depth + gate-design only): **6**
- Bucketed reasons: `{'no_atr_contraction': 24, 'too_extended': 19, 'no_breakout': 12, 'volume_insufficient': 12, 'insufficient_history_260': 6, 'unknown': 2}`

*cache_or_data_depth_artifact = killed only by the 260/75-bar history gate (shallow cache, not a structure failure); gate_design_mismatch = killed only by breakout/contraction/volume gates an EARLY leader is not meant to satisfy yet; real_quality_rejection = killed by a genuine structural reason (too extended, below MA200 floor). possibly_valid_early_candidates sums the first two — names a Lens/Gatekeeper second surface could legitimately surface.*

## Candidates

| ticker | source | stage | ELS | theme | ext | lens | gk | options | alpha-board | gate root | triage |
|---|---|---|--:|---|---|---|---|---|---|---|---|
| SATL | overlap | PULLBACK_RECLAIM | 63.4 | hardware | near_ema20 | Bearish but oversold | BLOCK | unusable | alpha_board_cap | real_quality | **NEEDS_LENS** |
| VOYG | overlap | PULLBACK_RECLAIM | 60.2 | space_aerospace | near_ema20 | Neutral | BLOCK | poor | alpha_board_cap | gate_design_mismatch | **NEEDS_LENS** |
| ASTS | overlap | PULLBACK_RECLAIM | 59.3 | space_aerospace | near_ema20 | Neutral | BLOCK | ok | alpha_board_cap | real_quality | **NEEDS_LENS** |
| FLY | overlap | PULLBACK_RECLAIM | 59.1 | space_aerospace | near_ema20 | Neutral | BLOCK | unusable | alpha_board_cap | gate_design_mismatch | **BLOCKED** |
| OPTX | overlap | PULLBACK_RECLAIM | 54.5 | hardware | near_ema20 | Neutral | BLOCK | — | alpha_board_cap | real_quality | **BLOCKED** |
| VPG | overlap | PULLBACK_RECLAIM | 53.0 | hardware | extended | — | — | — | alpha_board_cap | real_quality | **TOO_EXTENDED** |
| RKLB | overlap | PULLBACK_RECLAIM | 52.8 | space_aerospace | near_ema20 | Neutral | BLOCK | poor | alpha_board_cap | real_quality | **NEEDS_LENS** |
| OUST | overlap | PULLBACK_RECLAIM | 51.9 | semiconductors | near_ema20 | — | — | — | alpha_board_cap | real_quality | **NEEDS_LENS** |
| ALAB | overlap | PULLBACK_RECLAIM | 51.7 | semiconductors | near_ema20 | — | — | — | alpha_board_cap | real_quality | **NEEDS_LENS** |
| ATOM | overlap | PULLBACK_RECLAIM | 51.6 | semiconductors | near_ema20 | — | — | — | alpha_board_cap | real_quality | **NEEDS_LENS** |
| VSH | overlap | PULLBACK_RECLAIM | 49.7 | semiconductors | near_ema20 | — | — | — | alpha_board_cap | real_quality | **NEEDS_LENS** |
| NVTS | overlap | PULLBACK_RECLAIM | 44.0 | semiconductors | extended | — | — | — | alpha_board_cap | real_quality | **TOO_EXTENDED** |
| ENPH | overlap | PULLBACK_RECLAIM | 36.5 | other | near_ema20 | — | — | — | alpha_board_cap | real_quality | **NEEDS_LENS** |
| WOLF | overlap | PULLBACK_RECLAIM | 35.4 | semiconductors | extended | — | — | — | alpha_board_cap | real_quality | **TOO_EXTENDED** |
| AMBQ | overlap | LATE_EXTENDED | 16.8 | semiconductors | extended | — | — | — | alpha_board_cap | gate_design_mismatch | **TOO_EXTENDED** |
| VELO | overlap | LATE_EXTENDED | 16.0 | other | extended | — | — | — | alpha_board_cap | real_quality | **TOO_EXTENDED** |
| BIO | proposed_dynamic | EMERGING_MOMENTUM | 75.4 | biotech_healthcare | — | — | — | — | alpha_board_cap | passes_a_gate | **NOT_ENOUGH_DATA** |
| XPO | proposed_dynamic | BREAKOUT_CONFIRMED | 72.1 | other | near_ema20 | — | — | — | alpha_board_cap | passes_a_gate | **NEEDS_LENS** |
| LUNR | proposed_dynamic | PULLBACK_RECLAIM | 72.0 | space_aerospace | near_ema20 | — | — | — | alpha_board_cap | real_quality | **NEEDS_LENS** |
| APLS | proposed_dynamic | LOW_QUALITY_NOISE | 71.1 | biotech_healthcare | near_ema20 | — | — | — | alpha_board_cap | real_quality | **LOW_QUALITY_NOISE** |
| NTAP | theme | PULLBACK_RECLAIM | 70.9 | hardware | near_ema20 | — | — | — | alpha_board_cap | real_quality | **NEEDS_LENS** |
| LPTH | proposed_dynamic | PULLBACK_RECLAIM | 67.7 | hardware | near_ema20 | — | — | — | alpha_board_cap | passes_a_gate | **NEEDS_LENS** |
| COHR | proposed_dynamic | PULLBACK_RECLAIM | 67.5 | hardware | near_ema20 | Bullish but not buyable yet | BLOCK | poor | alpha_board_cap | real_quality | **NEEDS_GATEKEEPER** |
| DIA | proposed_dynamic | LOW_QUALITY_NOISE | 65.3 | other | near_ema20 | Bullish but not buyable yet | BLOCK | ok | alpha_board_cap | passes_a_gate | **LOW_QUALITY_NOISE** |
| RGTI | theme | PULLBACK_RECLAIM | 64.9 | hardware | near_ema20 | Avoid / no edge | BLOCK | unusable | alpha_board_cap | real_quality | **NEEDS_LENS** |
| AMPX | proposed_dynamic | LOW_QUALITY_NOISE | 62.0 | other | near_ema20 | — | — | — | alpha_board_cap | passes_a_gate | **LOW_QUALITY_NOISE** |
| RAL | proposed_dynamic | PULLBACK_RECLAIM | 61.2 | space_aerospace | near_ema20 | Bullish but not buyable yet | BLOCK | unusable | alpha_board_cap | gate_design_mismatch | **NEEDS_LENS** |
| ST | proposed_dynamic | PULLBACK_RECLAIM | 58.9 | hardware | near_ema20 | Bullish but not buyable yet | BLOCK | unusable | alpha_board_cap | gate_design_mismatch | **NEEDS_LENS** |
| BW | proposed_dynamic | PULLBACK_RECLAIM | 57.3 | other | near_ema20 | — | — | — | alpha_board_cap | real_quality | **NEEDS_LENS** |
| ANET | proposed_dynamic | LOW_QUALITY_NOISE | 57.1 | hardware | near_ema20 | Bullish but not buyable yet | WATCH | unusable | above_mcap_ceiling_80B | gate_design_mismatch | **LOW_QUALITY_NOISE** |

## Targeted refresh plan (Task 2 — design only, not executed)

DESIGN ONLY. No refresh is executed by this report. Run the commands below only with explicit operator approval.

- **build/refresh Stock Lens (PROVIDER calls — operator approval required)** — ~28 stock-lens builds (Alpaca bars + FMP profile/options per ticker)
  ```
  ./scripts/run_research_cycle.sh lens VPG OUST ALAB ATOM VSH NVTS ENPH WOLF AMBQ VELO BIO XPO LUNR APLS NTAP LPTH AMPX BW SATL VOYG ASTS FLY OPTX RKLB RGTI RAL ST ANET
  ```
- **refresh Executive Gatekeeper (cache-first; FMP earnings calendar only)** — ~10 gatekeeper rebuilds (cache-first, no per-ticker provider fan-out)
  ```
  ./scripts/run_research_cycle.sh gatekeeper-refresh --watch SATL VOYG ASTS RKLB COHR DIA RGTI RAL ST ANET
  ```

## Forward maturation

Each run appends today's triage to `data/research/rs_theme_lens_triage_history.jsonl` (idempotent per date/ticker). Forward outcomes will later answer whether research-watch names outperform, too-extended names pull back, the Lens/Gatekeeper rejected correctly, and whether RS/theme triage beats the Alpha board. No future data is stored today.

