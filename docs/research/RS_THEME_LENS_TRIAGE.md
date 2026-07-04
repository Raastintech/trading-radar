# RS/Theme → Lens/Gatekeeper Triage — Phase 1G.9

*Generated 2026-07-04T00:39:17.033149+00:00 · research-only · cache-only. Routing labels only — not buy/sell signals, not paper signals, not trade proposals. Does NOT modify the production universe, strategy gates, execution, or governance.*

**Verdict:** `PROMISING_RESEARCH_SURFACE`

## Why this surface exists
Phase 1G.8 found 333/356 proposed-dynamic early leaders are killed by the Voyager/Sniper structural gates, but most rejections are cache-depth artifacts. Its own recommendation was to route RS/theme early leaders to the Stock Lens/Gatekeeper as a research-only second surface that BYPASSES those score gates. This report is that surface — diagnostic only, no gate change.

## Triage quality summary (Task 4)

| metric | value |
|---|--:|
| candidates evaluated | 30 |
| needs Lens | 19 |
| needs Gatekeeper | 1 |
| Lens-ready (both artifacts fresh) | 0 |
| too extended | 1 |
| blocked | 3 |
| research-watch | 0 |
| low-quality noise | 4 |
| not enough data | 2 |
| with options confirmation | 10 |
| in leading themes | 16 |
| killed only by Alpha-board cap | 6 |
| killed by cache/gate artifact | 5 |

**Key question:** Would routing RS/theme leaders to Lens/Gatekeeper reveal useful candidates, or just create noise?

## Gate rejection decomposition (Task 3)

- Killed by both Voyager+Sniper gates: **23** / 30 evaluable.
- Root causes: `{'cache_or_data_depth_artifact': 0, 'gate_design_mismatch': 5, 'real_quality_rejection': 18, 'unknown': 0}`
- Possibly-valid early candidates (cache-depth + gate-design only): **5**
- Bucketed reasons: `{'no_atr_contraction': 20, 'too_extended': 18, 'volume_insufficient': 13, 'no_breakout': 13, 'insufficient_history_260': 5, 'unknown': 3}`

*cache_or_data_depth_artifact = killed only by the 260/75-bar history gate (shallow cache, not a structure failure); gate_design_mismatch = killed only by breakout/contraction/volume gates an EARLY leader is not meant to satisfy yet; real_quality_rejection = killed by a genuine structural reason (too extended, below MA200 floor). possibly_valid_early_candidates sums the first two — names a Lens/Gatekeeper second surface could legitimately surface.*

## Candidates

| ticker | source | stage | ELS | theme | ext | lens | gk | options | alpha-board | gate root | triage |
|---|---|---|--:|---|---|---|---|---|---|---|---|
| SATL | overlap | PULLBACK_RECLAIM | 64.9 | hardware | near_ema20 | Bearish but oversold | BLOCK | unusable | alpha_board_cap | real_quality | **NEEDS_LENS** |
| ASTS | overlap | PULLBACK_RECLAIM | 60.6 | space_aerospace | near_ema20 | Neutral | BLOCK | ok | alpha_board_cap | real_quality | **NEEDS_LENS** |
| VPG | overlap | PULLBACK_RECLAIM | 58.0 | hardware | near_ema20 | — | — | — | alpha_board_cap | real_quality | **NEEDS_LENS** |
| RKLB | overlap | PULLBACK_RECLAIM | 55.1 | space_aerospace | near_ema20 | Neutral | BLOCK | poor | alpha_board_cap | real_quality | **NEEDS_LENS** |
| OUST | overlap | PULLBACK_RECLAIM | 54.2 | semiconductors | near_ema20 | — | — | — | alpha_board_cap | real_quality | **NEEDS_LENS** |
| VOYG | overlap | BROKEN | 54.0 | space_aerospace | near_ema20 | Neutral | BLOCK | poor | alpha_board_cap | passes_a_gate | **LOW_QUALITY_NOISE** |
| OPTX | overlap | PULLBACK_RECLAIM | 53.8 | hardware | near_ema20 | Neutral | BLOCK | — | alpha_board_cap | real_quality | **BLOCKED** |
| ATOM | overlap | PULLBACK_RECLAIM | 53.8 | semiconductors | near_ema20 | — | — | — | alpha_board_cap | real_quality | **NEEDS_LENS** |
| VSH | overlap | PULLBACK_RECLAIM | 53.1 | semiconductors | near_ema20 | — | — | — | alpha_board_cap | real_quality | **NEEDS_LENS** |
| ALAB | overlap | PULLBACK_RECLAIM | 45.6 | semiconductors | near_ema20 | — | — | — | alpha_board_cap | real_quality | **NEEDS_LENS** |
| WOLF | overlap | PULLBACK_RECLAIM | 41.4 | semiconductors | near_ema20 | — | — | — | alpha_board_cap | real_quality | **NEEDS_LENS** |
| ENPH | overlap | PULLBACK_RECLAIM | 40.7 | other | near_ema20 | — | — | — | alpha_board_cap | real_quality | **NEEDS_LENS** |
| NVTS | overlap | PULLBACK_RECLAIM | 40.2 | semiconductors | near_ema20 | — | — | — | alpha_board_cap | real_quality | **NEEDS_LENS** |
| FLY | overlap | BROKEN | 38.0 | space_aerospace | constructive | Neutral | BLOCK | unusable | alpha_board_cap | gate_design_mismatch | **BLOCKED** |
| AMBQ | overlap | PULLBACK_RECLAIM | 34.4 | semiconductors | near_ema20 | — | — | — | alpha_board_cap | gate_design_mismatch | **NEEDS_LENS** |
| VELO | overlap | LATE_EXTENDED | 25.0 | other | extended | — | — | — | alpha_board_cap | real_quality | **TOO_EXTENDED** |
| LUNR | proposed_dynamic | PULLBACK_RECLAIM | 71.9 | space_aerospace | near_ema20 | — | — | — | alpha_board_cap | real_quality | **NEEDS_LENS** |
| APLS | proposed_dynamic | LOW_QUALITY_NOISE | 69.1 | biotech_healthcare | near_ema20 | — | — | — | alpha_board_cap | real_quality | **LOW_QUALITY_NOISE** |
| PACS | proposed_dynamic | EMERGING_MOMENTUM | 66.7 | other | — | — | — | — | alpha_board_cap | gate_design_mismatch | **NOT_ENOUGH_DATA** |
| DRS | proposed_dynamic | EMERGING_MOMENTUM | 66.2 | space_aerospace | — | — | — | — | alpha_board_cap | gate_design_mismatch | **NOT_ENOUGH_DATA** |
| RGTI | theme | PULLBACK_RECLAIM | 65.3 | hardware | near_ema20 | Avoid / no edge | BLOCK | unusable | alpha_board_cap | real_quality | **BLOCKED** |
| NTAP | theme | PULLBACK_RECLAIM | 64.5 | hardware | near_ema20 | — | — | — | alpha_board_cap | real_quality | **NEEDS_LENS** |
| COHR | proposed_dynamic | PULLBACK_RECLAIM | 64.3 | hardware | near_ema20 | Bullish but not buyable yet | BLOCK | poor | alpha_board_cap | real_quality | **NEEDS_GATEKEEPER** |
| LPTH | proposed_dynamic | PULLBACK_RECLAIM | 64.1 | hardware | near_ema20 | — | — | — | alpha_board_cap | passes_a_gate | **NEEDS_LENS** |
| ANET | proposed_dynamic | PULLBACK_RECLAIM | 64.0 | hardware | near_ema20 | Bullish but not buyable yet | WATCH | unusable | above_mcap_ceiling_80B | passes_a_gate | **NEEDS_LENS** |
| DIA | proposed_dynamic | LOW_QUALITY_NOISE | 63.7 | other | near_ema20 | Bullish but not buyable yet | BLOCK | ok | alpha_board_cap | passes_a_gate | **LOW_QUALITY_NOISE** |
| VIK | proposed_dynamic | PULLBACK_RECLAIM | 61.5 | other | near_ema20 | — | — | — | alpha_board_cap | passes_a_gate | **NEEDS_LENS** |
| ATRO | proposed_dynamic | PULLBACK_RECLAIM | 60.9 | space_aerospace | constructive | — | — | — | alpha_board_cap | passes_a_gate | **NEEDS_LENS** |
| RAL | proposed_dynamic | PULLBACK_RECLAIM | 60.2 | space_aerospace | near_ema20 | Bullish but not buyable yet | BLOCK | unusable | alpha_board_cap | gate_design_mismatch | **NEEDS_LENS** |
| AMPX | proposed_dynamic | LOW_QUALITY_NOISE | 60.0 | other | near_ema20 | — | — | — | alpha_board_cap | passes_a_gate | **LOW_QUALITY_NOISE** |

## Targeted refresh plan (Task 2 — design only, not executed)

DESIGN ONLY. No refresh is executed by this report. Run the commands below only with explicit operator approval.

- **build/refresh Stock Lens (PROVIDER calls — operator approval required)** — ~28 stock-lens builds (Alpaca bars + FMP profile/options per ticker)
  ```
  ./scripts/run_research_cycle.sh lens VPG OUST ATOM VSH ALAB WOLF ENPH NVTS AMBQ VELO LUNR APLS PACS DRS NTAP LPTH VIK ATRO AMPX SATL ASTS RKLB VOYG OPTX FLY RGTI ANET RAL
  ```
- **refresh Executive Gatekeeper (cache-first; FMP earnings calendar only)** — ~8 gatekeeper rebuilds (cache-first, no per-ticker provider fan-out)
  ```
  ./scripts/run_research_cycle.sh gatekeeper-refresh --watch SATL ASTS RKLB VOYG COHR ANET DIA RAL
  ```

## Forward maturation

Each run appends today's triage to `data/research/rs_theme_lens_triage_history.jsonl` (idempotent per date/ticker). Forward outcomes will later answer whether research-watch names outperform, too-extended names pull back, the Lens/Gatekeeper rejected correctly, and whether RS/theme triage beats the Alpha board. No future data is stored today.

