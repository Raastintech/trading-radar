# RS/Theme → Lens/Gatekeeper Triage — Phase 1G.9

*Generated 2026-06-19T17:02:23.746700+00:00 · research-only · cache-only. Routing labels only — not buy/sell signals, not paper signals, not trade proposals. Does NOT modify the production universe, strategy gates, execution, or governance.*

**Verdict:** `NEED_MORE_DATA`

## Why this surface exists
Phase 1G.8 found 333/356 proposed-dynamic early leaders are killed by the Voyager/Sniper structural gates, but most rejections are cache-depth artifacts. Its own recommendation was to route RS/theme early leaders to the Stock Lens/Gatekeeper as a research-only second surface that BYPASSES those score gates. This report is that surface — diagnostic only, no gate change.

## Triage quality summary (Task 4)

| metric | value |
|---|--:|
| candidates evaluated | 30 |
| needs Lens | 9 |
| needs Gatekeeper | 0 |
| Lens-ready (both artifacts fresh) | 0 |
| too extended | 10 |
| blocked | 4 |
| research-watch | 0 |
| low-quality noise | 7 |
| not enough data | 0 |
| with options confirmation | 9 |
| in leading themes | 16 |
| killed only by Alpha-board cap | 4 |
| killed by cache/gate artifact | 7 |

**Key question:** Would routing RS/theme leaders to Lens/Gatekeeper reveal useful candidates, or just create noise?

## Gate rejection decomposition (Task 3)

- Killed by both Voyager+Sniper gates: **26** / 30 evaluable.
- Root causes: `{'cache_or_data_depth_artifact': 0, 'gate_design_mismatch': 7, 'real_quality_rejection': 19, 'unknown': 0}`
- Possibly-valid early candidates (cache-depth + gate-design only): **7**
- Bucketed reasons: `{'no_atr_contraction': 25, 'too_extended': 18, 'volume_insufficient': 14, 'no_breakout': 12, 'insufficient_history_260': 7, 'unknown': 3, 'ma200_missing': 1}`

*cache_or_data_depth_artifact = killed only by the 260/75-bar history gate (shallow cache, not a structure failure); gate_design_mismatch = killed only by breakout/contraction/volume gates an EARLY leader is not meant to satisfy yet; real_quality_rejection = killed by a genuine structural reason (too extended, below MA200 floor). possibly_valid_early_candidates sums the first two — names a Lens/Gatekeeper second surface could legitimately surface.*

## Candidates

| ticker | source | stage | ELS | theme | ext | lens | gk | options | alpha-board | gate root | triage |
|---|---|---|--:|---|---|---|---|---|---|---|---|
| OPTX | overlap | BREAKOUT_CONFIRMED | 68.9 | hardware | near_ema20 | Neutral | BLOCK | — | alpha_board_cap | real_quality | **BLOCKED** |
| SATL | overlap | BREAKOUT_CONFIRMED | 62.1 | hardware | near_ema20 | Bearish but oversold | BLOCK | unusable | alpha_board_cap | real_quality | **NEEDS_LENS** |
| VOYG | overlap | BREAKOUT_CONFIRMED | 58.4 | space_aerospace | near_ema20 | Neutral | BLOCK | poor | alpha_board_cap | gate_design_mismatch | **NEEDS_LENS** |
| RKLB | overlap | BREAKOUT_CONFIRMED | 53.6 | space_aerospace | near_ema20 | Neutral | BLOCK | poor | alpha_board_cap | real_quality | **LOW_QUALITY_NOISE** |
| ASTS | overlap | BREAKOUT_CONFIRMED | 50.4 | space_aerospace | constructive | Neutral | BLOCK | ok | alpha_board_cap | real_quality | **BLOCKED** |
| FLY | overlap | BREAKOUT_CONFIRMED | 48.1 | space_aerospace | constructive | Neutral | BLOCK | unusable | alpha_board_cap | gate_design_mismatch | **BLOCKED** |
| ALAB | overlap | BREAKOUT_CONFIRMED | 47.4 | semiconductors | extended | — | — | — | alpha_board_cap | real_quality | **TOO_EXTENDED** |
| OUST | overlap | BREAKOUT_CONFIRMED | 46.9 | hardware | extended | — | — | — | alpha_board_cap | real_quality | **TOO_EXTENDED** |
| VPG | overlap | LATE_EXTENDED | 37.1 | hardware | extended | — | — | — | alpha_board_cap | real_quality | **TOO_EXTENDED** |
| VSH | overlap | BREAKOUT_CONFIRMED | 35.3 | semiconductors | extended | — | — | — | alpha_board_cap | real_quality | **TOO_EXTENDED** |
| ENPH | overlap | LATE_EXTENDED | 30.9 | other | extended | — | — | — | alpha_board_cap | real_quality | **TOO_EXTENDED** |
| NVTS | overlap | LATE_EXTENDED | 28.0 | semiconductors | extended | — | — | — | alpha_board_cap | real_quality | **TOO_EXTENDED** |
| ATOM | overlap | BREAKOUT_CONFIRMED | 27.9 | semiconductors | extended | — | — | — | alpha_board_cap | real_quality | **TOO_EXTENDED** |
| VELO | overlap | PARABOLIC | 20.0 | hardware | extended | — | — | — | alpha_board_cap | real_quality | **TOO_EXTENDED** |
| WOLF | overlap | LATE_EXTENDED | 19.0 | semiconductors | extended | — | — | — | alpha_board_cap | real_quality | **TOO_EXTENDED** |
| AMBQ | overlap | LATE_EXTENDED | 17.4 | semiconductors | extended | — | — | — | alpha_board_cap | gate_design_mismatch | **TOO_EXTENDED** |
| AMPX | proposed_dynamic | BREAKOUT_CONFIRMED | 77.3 | other | near_ema20 | — | — | — | alpha_board_cap | passes_a_gate | **NEEDS_LENS** |
| RGTI | theme | PULLBACK_RECLAIM | 74.4 | hardware | near_ema20 | Avoid / no edge | BLOCK | unusable | alpha_board_cap | real_quality | **BLOCKED** |
| XPO | proposed_dynamic | BREAKOUT_CONFIRMED | 71.1 | other | near_ema20 | — | — | — | alpha_board_cap | passes_a_gate | **NEEDS_LENS** |
| APLS | proposed_dynamic | PULLBACK_RECLAIM | 70.6 | biotech_healthcare | near_ema20 | — | — | — | alpha_board_cap | real_quality | **NEEDS_LENS** |
| NTAP | theme | PULLBACK_RECLAIM | 69.4 | hardware | near_ema20 | — | — | — | alpha_board_cap | real_quality | **NEEDS_LENS** |
| COHR | proposed_dynamic | BREAKOUT_CONFIRMED | 67.3 | hardware | near_ema20 | Neutral | BLOCK | poor | alpha_board_cap | real_quality | **LOW_QUALITY_NOISE** |
| INFQ | proposed_dynamic | LOW_QUALITY_NOISE | 67.2 | hardware | near_ema20 | — | — | — | alpha_board_cap | gate_design_mismatch | **LOW_QUALITY_NOISE** |
| PSN | proposed_dynamic | EMERGING_MOMENTUM | 66.7 | other | near_ema20 | — | — | — | alpha_board_cap | real_quality | **NEEDS_LENS** |
| LUNR | proposed_dynamic | PULLBACK_RECLAIM | 65.7 | space_aerospace | near_ema20 | — | — | — | alpha_board_cap | real_quality | **NEEDS_LENS** |
| DIA | proposed_dynamic | LOW_QUALITY_NOISE | 65.2 | other | near_ema20 | Bullish but not buyable yet | BLOCK | ok | alpha_board_cap | passes_a_gate | **LOW_QUALITY_NOISE** |
| BTU | proposed_dynamic | BROKEN | 64.0 | other | near_ema20 | — | — | — | alpha_board_cap | gate_design_mismatch | **LOW_QUALITY_NOISE** |
| BURL | proposed_dynamic | BREAKOUT_CONFIRMED | 63.2 | other | near_ema20 | — | — | — | alpha_board_cap | gate_design_mismatch | **NEEDS_LENS** |
| ANET | proposed_dynamic | LOW_QUALITY_NOISE | 63.0 | hardware | near_ema20 | Bullish but not buyable yet | WATCH | unusable | above_mcap_ceiling_80B | gate_design_mismatch | **LOW_QUALITY_NOISE** |
| LPTH | proposed_dynamic | LOW_QUALITY_NOISE | 62.5 | hardware | near_ema20 | — | — | — | alpha_board_cap | passes_a_gate | **LOW_QUALITY_NOISE** |

## Targeted refresh plan (Task 2 — design only, not executed)

DESIGN ONLY. No refresh is executed by this report. Run the commands below only with explicit operator approval.

- **build/refresh Stock Lens (PROVIDER calls — operator approval required)** — ~27 stock-lens builds (Alpaca bars + FMP profile/options per ticker)
  ```
  ./scripts/run_research_cycle.sh lens ALAB OUST VPG VSH ENPH NVTS ATOM VELO WOLF AMBQ AMPX XPO APLS NTAP INFQ PSN LUNR BTU BURL LPTH OPTX SATL VOYG ASTS FLY RGTI ANET
  ```
- **refresh Executive Gatekeeper (cache-first; FMP earnings calendar only)** — ~6 gatekeeper rebuilds (cache-first, no per-ticker provider fan-out)
  ```
  ./scripts/run_research_cycle.sh gatekeeper-refresh --watch SATL VOYG RKLB COHR DIA ANET
  ```

## Forward maturation

Each run appends today's triage to `data/research/rs_theme_lens_triage_history.jsonl` (idempotent per date/ticker). Forward outcomes will later answer whether research-watch names outperform, too-extended names pull back, the Lens/Gatekeeper rejected correctly, and whether RS/theme triage beats the Alpha board. No future data is stored today.

