# RS/Theme → Lens/Gatekeeper Triage — Phase 1G.9

*Generated 2026-06-16T05:51:37.856812+00:00 · research-only · cache-only. Routing labels only — not buy/sell signals, not paper signals, not trade proposals. Does NOT modify the production universe, strategy gates, execution, or governance.*

**Verdict:** `NEED_MORE_DATA`

## Why this surface exists
Phase 1G.8 found 333/356 proposed-dynamic early leaders are killed by the Voyager/Sniper structural gates, but most rejections are cache-depth artifacts. Its own recommendation was to route RS/theme early leaders to the Stock Lens/Gatekeeper as a research-only second surface that BYPASSES those score gates. This report is that surface — diagnostic only, no gate change.

## Triage quality summary (Task 4)

| metric | value |
|---|--:|
| candidates evaluated | 30 |
| needs Lens | 10 |
| needs Gatekeeper | 1 |
| Lens-ready (both artifacts fresh) | 0 |
| too extended | 12 |
| blocked | 3 |
| research-watch | 0 |
| low-quality noise | 4 |
| not enough data | 0 |
| with options confirmation | 8 |
| in leading themes | 12 |
| killed only by Alpha-board cap | 5 |
| killed by cache/gate artifact | 8 |

**Key question:** Would routing RS/theme leaders to Lens/Gatekeeper reveal useful candidates, or just create noise?

## Gate rejection decomposition (Task 3)

- Killed by both Voyager+Sniper gates: **25** / 30 evaluable.
- Root causes: `{'cache_or_data_depth_artifact': 0, 'gate_design_mismatch': 8, 'real_quality_rejection': 17, 'unknown': 0}`
- Possibly-valid early candidates (cache-depth + gate-design only): **8**
- Bucketed reasons: `{'no_atr_contraction': 24, 'volume_insufficient': 16, 'too_extended': 15, 'no_breakout': 11, 'insufficient_history_260': 8, 'unknown': 3, 'ma200_missing': 2}`

*cache_or_data_depth_artifact = killed only by the 260/75-bar history gate (shallow cache, not a structure failure); gate_design_mismatch = killed only by breakout/contraction/volume gates an EARLY leader is not meant to satisfy yet; real_quality_rejection = killed by a genuine structural reason (too extended, below MA200 floor). possibly_valid_early_candidates sums the first two — names a Lens/Gatekeeper second surface could legitimately surface.*

## Candidates

| ticker | source | stage | ELS | theme | ext | lens | gk | options | alpha-board | gate root | triage |
|---|---|---|--:|---|---|---|---|---|---|---|---|
| OPTX | overlap | BREAKOUT_CONFIRMED | 62.9 | hardware | constructive | Neutral | BLOCK | — | alpha_board_cap | real_quality | **BLOCKED** |
| ASTS | overlap | BREAKOUT_CONFIRMED | 56.3 | space_aerospace | constructive | Neutral | BLOCK | ok | alpha_board_cap | real_quality | **BLOCKED** |
| FLY | overlap | BREAKOUT_CONFIRMED | 52.2 | space_aerospace | extended | Neutral | BLOCK | unusable | alpha_board_cap | gate_design_mismatch | **BLOCKED** |
| SATL | overlap | BREAKOUT_CONFIRMED | 47.1 | hardware | constructive | Bearish but oversold | BLOCK | unusable | alpha_board_cap | real_quality | **LOW_QUALITY_NOISE** |
| VOYG | overlap | BREAKOUT_CONFIRMED | 45.4 | space_aerospace | extended | Neutral | BLOCK | poor | alpha_board_cap | gate_design_mismatch | **TOO_EXTENDED** |
| RKLB | overlap | BREAKOUT_CONFIRMED | 43.4 | space_aerospace | extended | Neutral | BLOCK | ok | alpha_board_cap | real_quality | **TOO_EXTENDED** |
| OUST | overlap | BREAKOUT_CONFIRMED | 41.3 | hardware | extended | — | — | — | alpha_board_cap | real_quality | **TOO_EXTENDED** |
| VPG | overlap | LATE_EXTENDED | 36.0 | hardware | extended | — | — | — | alpha_board_cap | real_quality | **TOO_EXTENDED** |
| ALAB | overlap | LATE_EXTENDED | 31.8 | semiconductors | extended | — | — | — | alpha_board_cap | real_quality | **TOO_EXTENDED** |
| VSH | overlap | LATE_EXTENDED | 28.5 | semiconductors | extended | — | — | — | alpha_board_cap | real_quality | **TOO_EXTENDED** |
| ENPH | overlap | LATE_EXTENDED | 28.0 | other | extended | — | — | — | alpha_board_cap | real_quality | **TOO_EXTENDED** |
| AMBQ | overlap | LATE_EXTENDED | 28.0 | semiconductors | extended | — | — | — | alpha_board_cap | gate_design_mismatch | **TOO_EXTENDED** |
| VELO | overlap | LATE_EXTENDED | 25.0 | hardware | extended | — | — | — | alpha_board_cap | gate_design_mismatch | **TOO_EXTENDED** |
| ATOM | overlap | LATE_EXTENDED | 20.8 | semiconductors | extended | — | — | — | alpha_board_cap | real_quality | **TOO_EXTENDED** |
| WOLF | overlap | LATE_EXTENDED | 19.0 | semiconductors | extended | — | — | — | alpha_board_cap | real_quality | **TOO_EXTENDED** |
| NVTS | overlap | LATE_EXTENDED | 16.0 | semiconductors | extended | — | — | — | alpha_board_cap | real_quality | **TOO_EXTENDED** |
| XPO | proposed_dynamic | BREAKOUT_CONFIRMED | 70.8 | other | near_ema20 | — | — | — | alpha_board_cap | passes_a_gate | **NEEDS_LENS** |
| APLS | proposed_dynamic | PULLBACK_RECLAIM | 70.4 | biotech_healthcare | near_ema20 | — | — | — | alpha_board_cap | real_quality | **NEEDS_LENS** |
| DIA | proposed_dynamic | EARLY_ACCUMULATION | 68.1 | other | near_ema20 | Bullish but not buyable yet | BLOCK | unusable | alpha_board_cap | passes_a_gate | **NEEDS_GATEKEEPER** |
| BIO | proposed_dynamic | EMERGING_MOMENTUM | 67.7 | biotech_healthcare | near_ema20 | — | — | — | alpha_board_cap | passes_a_gate | **NEEDS_LENS** |
| INFQ | proposed_dynamic | LOW_QUALITY_NOISE | 65.6 | hardware | near_ema20 | — | — | — | alpha_board_cap | gate_design_mismatch | **LOW_QUALITY_NOISE** |
| PSN | proposed_dynamic | EMERGING_MOMENTUM | 65.6 | other | near_ema20 | — | — | — | alpha_board_cap | real_quality | **NEEDS_LENS** |
| MT | proposed_dynamic | BREAKOUT_CONFIRMED | 64.0 | other | near_ema20 | — | — | — | alpha_board_cap | gate_design_mismatch | **NEEDS_LENS** |
| DECK | proposed_dynamic | BREAKOUT_CONFIRMED | 62.8 | other | near_ema20 | — | — | — | alpha_board_cap | gate_design_mismatch | **NEEDS_LENS** |
| LUNR | proposed_dynamic | PULLBACK_RECLAIM | 62.7 | space_aerospace | near_ema20 | — | — | — | alpha_board_cap | real_quality | **NEEDS_LENS** |
| AMPX | proposed_dynamic | BREAKOUT_CONFIRMED | 61.8 | other | near_ema20 | — | — | — | alpha_board_cap | passes_a_gate | **NEEDS_LENS** |
| SPOT | proposed_dynamic | LOW_QUALITY_NOISE | 60.7 | other | near_ema20 | Bearish | WATCH | ok | on_alpha_board | real_quality | **LOW_QUALITY_NOISE** |
| VIK | proposed_dynamic | BREAKOUT_CONFIRMED | 60.0 | other | near_ema20 | — | — | — | alpha_board_cap | passes_a_gate | **NEEDS_LENS** |
| LPTH | proposed_dynamic | LOW_QUALITY_NOISE | 59.7 | hardware | near_ema20 | — | — | — | alpha_board_cap | gate_design_mismatch | **LOW_QUALITY_NOISE** |
| ILMN | proposed_dynamic | PULLBACK_RECLAIM | 59.2 | biotech_healthcare | near_ema20 | Bullish but not buyable yet | WATCH | poor | alpha_board_cap | real_quality | **NEEDS_LENS** |

## Targeted refresh plan (Task 2 — design only, not executed)

DESIGN ONLY. No refresh is executed by this report. Run the commands below only with explicit operator approval.

- **build/refresh Stock Lens (PROVIDER calls — operator approval required)** — ~23 stock-lens builds (Alpaca bars + FMP profile/options per ticker)
  ```
  ./scripts/run_research_cycle.sh lens OUST VPG ALAB VSH ENPH AMBQ VELO ATOM WOLF NVTS XPO APLS BIO INFQ PSN MT DECK LUNR AMPX VIK LPTH RKLB ILMN
  ```
- **refresh Executive Gatekeeper (cache-first; FMP earnings calendar only)** — ~5 gatekeeper rebuilds (cache-first, no per-ticker provider fan-out)
  ```
  ./scripts/run_research_cycle.sh gatekeeper-refresh --watch SATL VOYG RKLB DIA SPOT
  ```

## Forward maturation

Each run appends today's triage to `data/research/rs_theme_lens_triage_history.jsonl` (idempotent per date/ticker). Forward outcomes will later answer whether research-watch names outperform, too-extended names pull back, the Lens/Gatekeeper rejected correctly, and whether RS/theme triage beats the Alpha board. No future data is stored today.

