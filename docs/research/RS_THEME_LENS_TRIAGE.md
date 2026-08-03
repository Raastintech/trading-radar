# RS/Theme → Lens/Gatekeeper Triage — Phase 1G.9

*Generated 2026-08-01T00:36:19.206600+00:00 · research-only · cache-only. Routing labels only — not buy/sell signals, not paper signals, not trade proposals. Does NOT modify the production universe, strategy gates, execution, or governance.*

**Verdict:** `NEED_MORE_DATA`

## Why this surface exists
Phase 1G.8 found 333/356 proposed-dynamic early leaders are killed by the Voyager/Sniper structural gates, but most rejections are cache-depth artifacts. Its own recommendation was to route RS/theme early leaders to the Stock Lens/Gatekeeper as a research-only second surface that BYPASSES those score gates. This report is that surface — diagnostic only, no gate change.

## Triage quality summary (Task 4)

| metric | value |
|---|--:|
| candidates evaluated | 30 |
| needs Lens | 13 |
| needs Gatekeeper | 0 |
| Lens-ready (both artifacts fresh) | 0 |
| too extended | 0 |
| blocked | 0 |
| research-watch | 0 |
| low-quality noise | 14 |
| not enough data | 3 |
| with options confirmation | 9 |
| in leading themes | 14 |
| killed only by Alpha-board cap | 5 |
| killed by cache/gate artifact | 12 |

**Key question:** Would routing RS/theme leaders to Lens/Gatekeeper reveal useful candidates, or just create noise?

## Gate rejection decomposition (Task 3)

- Killed by both Voyager+Sniper gates: **25** / 30 evaluable.
- Root causes: `{'cache_or_data_depth_artifact': 0, 'gate_design_mismatch': 12, 'real_quality_rejection': 13, 'unknown': 0}`
- Possibly-valid early candidates (cache-depth + gate-design only): **12**
- Bucketed reasons: `{'unknown': 28, 'no_breakout': 23, 'volume_insufficient': 22, 'no_atr_contraction': 11, 'ma200_missing': 7, 'too_extended': 6, 'insufficient_history_260': 2}`

*cache_or_data_depth_artifact = killed only by the 260/75-bar history gate (shallow cache, not a structure failure); gate_design_mismatch = killed only by breakout/contraction/volume gates an EARLY leader is not meant to satisfy yet; real_quality_rejection = killed by a genuine structural reason (too extended, below MA200 floor). possibly_valid_early_candidates sums the first two — names a Lens/Gatekeeper second surface could legitimately surface.*

## Candidates

| ticker | source | stage | ELS | theme | ext | lens | gk | options | alpha-board | gate root | triage |
|---|---|---|--:|---|---|---|---|---|---|---|---|
| WOLF | overlap | PULLBACK_RECLAIM | 53.8 | semiconductors | near_ema20 | — | — | — | alpha_board_cap | real_quality | **NEEDS_LENS** |
| VELO | overlap | PULLBACK_RECLAIM | 49.9 | other | near_ema20 | — | — | — | below_mcap_floor_300M | real_quality | **NEEDS_LENS** |
| FLY | overlap | BROKEN | 46.0 | space_aerospace | constructive | Neutral | BLOCK | unusable | alpha_board_cap | gate_design_mismatch | **LOW_QUALITY_NOISE** |
| ASTS | overlap | BROKEN | 46.0 | space_aerospace | constructive | Neutral | BLOCK | ok | alpha_board_cap | real_quality | **LOW_QUALITY_NOISE** |
| VPG | overlap | BROKEN | 43.0 | hardware | constructive | — | — | — | alpha_board_cap | gate_design_mismatch | **LOW_QUALITY_NOISE** |
| VOYG | overlap | BROKEN | 43.0 | space_aerospace | constructive | Neutral | BLOCK | poor | alpha_board_cap | real_quality | **LOW_QUALITY_NOISE** |
| RKLB | overlap | BROKEN | 43.0 | space_aerospace | constructive | Neutral | BLOCK | poor | alpha_board_cap | real_quality | **LOW_QUALITY_NOISE** |
| ENPH | overlap | BROKEN | 38.0 | other | constructive | — | WATCH | — | alpha_board_cap | gate_design_mismatch | **LOW_QUALITY_NOISE** |
| NVTS | overlap | BROKEN | 35.0 | semiconductors | constructive | — | WATCH | — | alpha_board_cap | real_quality | **LOW_QUALITY_NOISE** |
| VSH | overlap | BROKEN | 35.0 | semiconductors | constructive | — | — | — | alpha_board_cap | gate_design_mismatch | **LOW_QUALITY_NOISE** |
| ALAB | overlap | BROKEN | 30.0 | semiconductors | constructive | — | WATCH | — | alpha_board_cap | gate_design_mismatch | **LOW_QUALITY_NOISE** |
| OUST | overlap | BROKEN | 30.0 | semiconductors | constructive | — | — | — | alpha_board_cap | gate_design_mismatch | **LOW_QUALITY_NOISE** |
| AMBQ | overlap | BROKEN | 27.0 | semiconductors | constructive | — | WATCH | — | alpha_board_cap | gate_design_mismatch | **LOW_QUALITY_NOISE** |
| OPTX | overlap | — | — | hardware | — | Neutral | BLOCK | — | alpha_board_cap | real_quality | **NOT_ENOUGH_DATA** |
| ATOM | overlap | — | — | semiconductors | — | — | — | — | alpha_board_cap | gate_design_mismatch | **NOT_ENOUGH_DATA** |
| SATL | overlap | — | — | hardware | — | Bearish but oversold | BLOCK | unusable | alpha_board_cap | real_quality | **NOT_ENOUGH_DATA** |
| NTAP | theme | PULLBACK_RECLAIM | 78.6 | hardware | near_ema20 | — | — | — | alpha_board_cap | real_quality | **NEEDS_LENS** |
| LPTH | proposed_dynamic | PULLBACK_RECLAIM | 69.8 | hardware | near_ema20 | — | — | — | alpha_board_cap | passes_a_gate | **NEEDS_LENS** |
| BTU | proposed_dynamic | BREAKOUT_CONFIRMED | 68.5 | other | near_ema20 | — | WATCH | — | alpha_board_cap | real_quality | **NEEDS_LENS** |
| ST | proposed_dynamic | BREAKOUT_CONFIRMED | 67.6 | hardware | near_ema20 | Bullish but not buyable yet | BLOCK | unusable | alpha_board_cap | passes_a_gate | **NEEDS_LENS** |
| VIST | proposed_dynamic | BREAKOUT_CONFIRMED | 66.5 | other | near_ema20 | — | — | — | alpha_board_cap | gate_design_mismatch | **NEEDS_LENS** |
| DRS | proposed_dynamic | LOW_QUALITY_NOISE | 65.5 | space_aerospace | near_ema20 | — | — | — | alpha_board_cap | passes_a_gate | **LOW_QUALITY_NOISE** |
| CAH | proposed_dynamic | PULLBACK_RECLAIM | 65.3 | biotech_healthcare | near_ema20 | — | — | — | alpha_board_cap | passes_a_gate | **NEEDS_LENS** |
| F | proposed_dynamic | BREAKOUT_CONFIRMED | 65.1 | other | near_ema20 | Bullish but not buyable yet | BLOCK | unusable | alpha_board_cap | gate_design_mismatch | **NEEDS_LENS** |
| COHR | proposed_dynamic | LOW_QUALITY_NOISE | 63.0 | hardware | near_ema20 | Neutral | BLOCK | unusable | alpha_board_cap | gate_design_mismatch | **LOW_QUALITY_NOISE** |
| QTTB | theme | PULLBACK_RECLAIM | 62.4 | biotech_healthcare | near_ema20 | — | — | — | below_mcap_floor_300M | real_quality | **NEEDS_LENS** |
| UMAC | theme | PULLBACK_RECLAIM | 61.3 | other | near_ema20 | — | — | — | alpha_board_cap | real_quality | **NEEDS_LENS** |
| MT | proposed_dynamic | BREAKOUT_CONFIRMED | 61.2 | other | near_ema20 | — | — | — | alpha_board_cap | passes_a_gate | **NEEDS_LENS** |
| ANET | proposed_dynamic | LOW_QUALITY_NOISE | 60.8 | hardware | near_ema20 | Bullish but not buyable yet | WATCH | unusable | above_mcap_ceiling_80B | gate_design_mismatch | **LOW_QUALITY_NOISE** |
| RDW | theme | PULLBACK_RECLAIM | 60.7 | space_aerospace | near_ema20 | — | — | — | alpha_board_cap | real_quality | **NEEDS_LENS** |

## Targeted refresh plan (Task 2 — design only, not executed)

DESIGN ONLY. No refresh is executed by this report. Run the commands below only with explicit operator approval.

- **build/refresh Stock Lens (PROVIDER calls — operator approval required)** — ~30 stock-lens builds (Alpaca bars + FMP profile/options per ticker)
  ```
  ./scripts/run_research_cycle.sh lens WOLF VELO VPG ENPH NVTS VSH ALAB OUST AMBQ ATOM NTAP LPTH BTU VIST DRS CAH QTTB UMAC MT RDW FLY ASTS VOYG RKLB OPTX SATL ST F COHR ANET
  ```
- **refresh Executive Gatekeeper (cache-first; FMP earnings calendar only)** — ~12 gatekeeper rebuilds (cache-first, no per-ticker provider fan-out)
  ```
  ./scripts/run_research_cycle.sh gatekeeper-refresh --watch FLY ASTS VOYG RKLB ENPH NVTS AMBQ OPTX SATL ST F COHR
  ```

## Forward maturation

Each run appends today's triage to `data/research/rs_theme_lens_triage_history.jsonl` (idempotent per date/ticker). Forward outcomes will later answer whether research-watch names outperform, too-extended names pull back, the Lens/Gatekeeper rejected correctly, and whether RS/theme triage beats the Alpha board. No future data is stored today.

