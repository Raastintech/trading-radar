# RS/Theme → Lens/Gatekeeper Triage — Phase 1G.9

*Generated 2026-06-15T01:04:26.975804+00:00 · research-only · cache-only. Routing labels only — not buy/sell signals, not paper signals, not trade proposals. Does NOT modify the production universe, strategy gates, execution, or governance.*

**Verdict:** `NEED_MORE_DATA`

## Why this surface exists
Phase 1G.8 found 333/356 proposed-dynamic early leaders are killed by the Voyager/Sniper structural gates, but most rejections are cache-depth artifacts. Its own recommendation was to route RS/theme early leaders to the Stock Lens/Gatekeeper as a research-only second surface that BYPASSES those score gates. This report is that surface — diagnostic only, no gate change.

## Triage quality summary (Task 4)

| metric | value |
|---|--:|
| candidates evaluated | 30 |
| needs Lens | 9 |
| needs Gatekeeper | 1 |
| Lens-ready (both artifacts fresh) | 0 |
| too extended | 15 |
| blocked | 1 |
| research-watch | 0 |
| low-quality noise | 4 |
| not enough data | 0 |
| with options confirmation | 9 |
| in leading themes | 13 |
| killed only by Alpha-board cap | 3 |
| killed by cache/gate artifact | 9 |

**Key question:** Would routing RS/theme leaders to Lens/Gatekeeper reveal useful candidates, or just create noise?

## Gate rejection decomposition (Task 3)

- Killed by both Voyager+Sniper gates: **27** / 30 evaluable.
- Root causes: `{'cache_or_data_depth_artifact': 0, 'gate_design_mismatch': 9, 'real_quality_rejection': 18, 'unknown': 0}`
- Possibly-valid early candidates (cache-depth + gate-design only): **9**
- Bucketed reasons: `{'no_atr_contraction': 26, 'volume_insufficient': 17, 'too_extended': 16, 'no_breakout': 12, 'insufficient_history_260': 9, 'unknown': 4, 'ma200_missing': 2}`

*cache_or_data_depth_artifact = killed only by the 260/75-bar history gate (shallow cache, not a structure failure); gate_design_mismatch = killed only by breakout/contraction/volume gates an EARLY leader is not meant to satisfy yet; real_quality_rejection = killed by a genuine structural reason (too extended, below MA200 floor). possibly_valid_early_candidates sums the first two — names a Lens/Gatekeeper second surface could legitimately surface.*

## Candidates

| ticker | source | stage | ELS | theme | ext | lens | gk | options | alpha-board | gate root | triage |
|---|---|---|--:|---|---|---|---|---|---|---|---|
| ASTS | overlap | BREAKOUT_CONFIRMED | 55.6 | space_aerospace | extended | Neutral | WATCH | ok | alpha_board_cap | real_quality | **TOO_EXTENDED** |
| SATL | overlap | BREAKOUT_CONFIRMED | 48.0 | hardware | extended | Bearish but oversold | BLOCK | unusable | alpha_board_cap | real_quality | **TOO_EXTENDED** |
| FLY | overlap | BREAKOUT_CONFIRMED | 47.7 | space_aerospace | extended | Neutral | BLOCK | unusable | alpha_board_cap | gate_design_mismatch | **BLOCKED** |
| OPTX | overlap | BREAKOUT_CONFIRMED | 47.0 | hardware | extended | Neutral | BLOCK | — | alpha_board_cap | real_quality | **TOO_EXTENDED** |
| VOYG | overlap | BREAKOUT_CONFIRMED | 43.9 | space_aerospace | extended | Neutral | BLOCK | poor | alpha_board_cap | gate_design_mismatch | **TOO_EXTENDED** |
| OUST | overlap | LATE_EXTENDED | 39.3 | hardware | extended | — | — | — | alpha_board_cap | real_quality | **TOO_EXTENDED** |
| VPG | overlap | LATE_EXTENDED | 33.0 | hardware | extended | — | — | — | alpha_board_cap | real_quality | **TOO_EXTENDED** |
| RKLB | overlap | BREAKOUT_CONFIRMED | 31.4 | space_aerospace | extended | Neutral | BLOCK | ok | alpha_board_cap | real_quality | **TOO_EXTENDED** |
| ALAB | overlap | LATE_EXTENDED | 29.4 | semiconductors | extended | — | — | — | alpha_board_cap | real_quality | **TOO_EXTENDED** |
| VSH | overlap | LATE_EXTENDED | 28.0 | semiconductors | extended | — | — | — | alpha_board_cap | real_quality | **TOO_EXTENDED** |
| AMBQ | overlap | LATE_EXTENDED | 28.0 | semiconductors | extended | — | — | — | alpha_board_cap | gate_design_mismatch | **TOO_EXTENDED** |
| ENPH | overlap | LATE_EXTENDED | 25.0 | other | extended | — | — | — | alpha_board_cap | real_quality | **TOO_EXTENDED** |
| VELO | overlap | LATE_EXTENDED | 22.0 | hardware | extended | — | — | — | alpha_board_cap | gate_design_mismatch | **TOO_EXTENDED** |
| NVTS | overlap | LATE_EXTENDED | 16.0 | semiconductors | extended | — | — | — | alpha_board_cap | real_quality | **TOO_EXTENDED** |
| ATOM | overlap | LATE_EXTENDED | 16.0 | semiconductors | extended | — | — | — | alpha_board_cap | real_quality | **TOO_EXTENDED** |
| WOLF | overlap | LATE_EXTENDED | 15.0 | semiconductors | extended | — | — | — | alpha_board_cap | real_quality | **TOO_EXTENDED** |
| APLS | proposed_dynamic | PULLBACK_RECLAIM | 71.3 | biotech_healthcare | near_ema20 | — | — | — | alpha_board_cap | real_quality | **NEEDS_LENS** |
| XPO | proposed_dynamic | BREAKOUT_CONFIRMED | 70.1 | other | near_ema20 | — | — | — | alpha_board_cap | passes_a_gate | **NEEDS_LENS** |
| DIA | proposed_dynamic | EARLY_ACCUMULATION | 69.9 | other | near_ema20 | Bullish but not buyable yet | BLOCK | poor | alpha_board_cap | passes_a_gate | **NEEDS_GATEKEEPER** |
| BIO | proposed_dynamic | EMERGING_MOMENTUM | 67.6 | biotech_healthcare | near_ema20 | — | — | — | alpha_board_cap | passes_a_gate | **NEEDS_LENS** |
| PSN | proposed_dynamic | EMERGING_MOMENTUM | 65.6 | other | near_ema20 | — | — | — | alpha_board_cap | real_quality | **NEEDS_LENS** |
| SPOT | proposed_dynamic | LOW_QUALITY_NOISE | 62.7 | other | near_ema20 | Bearish | WATCH | poor | on_alpha_board | real_quality | **LOW_QUALITY_NOISE** |
| LUNR | proposed_dynamic | PULLBACK_RECLAIM | 61.1 | space_aerospace | near_ema20 | — | — | — | alpha_board_cap | real_quality | **NEEDS_LENS** |
| BTU | proposed_dynamic | BROKEN | 59.9 | other | near_ema20 | — | — | — | alpha_board_cap | gate_design_mismatch | **LOW_QUALITY_NOISE** |
| ILMN | proposed_dynamic | PULLBACK_RECLAIM | 59.4 | biotech_healthcare | near_ema20 | Bullish but not buyable yet | WATCH | poor | alpha_board_cap | real_quality | **NEEDS_LENS** |
| LPTH | proposed_dynamic | LOW_QUALITY_NOISE | 58.0 | hardware | near_ema20 | — | — | — | alpha_board_cap | gate_design_mismatch | **LOW_QUALITY_NOISE** |
| INFQ | proposed_dynamic | LOW_QUALITY_NOISE | 57.8 | hardware | near_ema20 | — | — | — | alpha_board_cap | gate_design_mismatch | **LOW_QUALITY_NOISE** |
| MT | proposed_dynamic | BREAKOUT_CONFIRMED | 57.7 | other | near_ema20 | — | — | — | alpha_board_cap | gate_design_mismatch | **NEEDS_LENS** |
| RGTI | theme | EMERGING_MOMENTUM | 57.1 | hardware | constructive | Avoid / no edge | BLOCK | unusable | alpha_board_cap | real_quality | **NEEDS_LENS** |
| JHX | proposed_dynamic | BREAKOUT_CONFIRMED | 55.4 | other | constructive | — | — | — | alpha_board_cap | gate_design_mismatch | **NEEDS_LENS** |

## Targeted refresh plan (Task 2 — design only, not executed)

DESIGN ONLY. No refresh is executed by this report. Run the commands below only with explicit operator approval.

- **build/refresh Stock Lens (PROVIDER calls — operator approval required)** — ~23 stock-lens builds (Alpaca bars + FMP profile/options per ticker)
  ```
  ./scripts/run_research_cycle.sh lens OUST VPG ALAB VSH AMBQ ENPH VELO NVTS ATOM WOLF APLS XPO BIO PSN LUNR BTU LPTH INFQ MT JHX RKLB ILMN RGTI
  ```
- **refresh Executive Gatekeeper (cache-first; FMP earnings calendar only)** — ~8 gatekeeper rebuilds (cache-first, no per-ticker provider fan-out)
  ```
  ./scripts/run_research_cycle.sh gatekeeper-refresh --watch ASTS SATL OPTX VOYG RKLB DIA SPOT RGTI
  ```

## Forward maturation

Each run appends today's triage to `data/research/rs_theme_lens_triage_history.jsonl` (idempotent per date/ticker). Forward outcomes will later answer whether research-watch names outperform, too-extended names pull back, the Lens/Gatekeeper rejected correctly, and whether RS/theme triage beats the Alpha board. No future data is stored today.

