# RS/Theme → Lens/Gatekeeper Triage — Phase 1G.9

*Generated 2026-06-18T00:32:21.970209+00:00 · research-only · cache-only. Routing labels only — not buy/sell signals, not paper signals, not trade proposals. Does NOT modify the production universe, strategy gates, execution, or governance.*

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
| too extended | 11 |
| blocked | 4 |
| research-watch | 0 |
| low-quality noise | 5 |
| not enough data | 0 |
| with options confirmation | 9 |
| in leading themes | 16 |
| killed only by Alpha-board cap | 4 |
| killed by cache/gate artifact | 9 |

**Key question:** Would routing RS/theme leaders to Lens/Gatekeeper reveal useful candidates, or just create noise?

## Gate rejection decomposition (Task 3)

- Killed by both Voyager+Sniper gates: **26** / 30 evaluable.
- Root causes: `{'cache_or_data_depth_artifact': 0, 'gate_design_mismatch': 9, 'real_quality_rejection': 17, 'unknown': 0}`
- Possibly-valid early candidates (cache-depth + gate-design only): **9**
- Bucketed reasons: `{'no_atr_contraction': 25, 'too_extended': 16, 'volume_insufficient': 16, 'no_breakout': 11, 'insufficient_history_260': 9, 'unknown': 2, 'ma200_missing': 1}`

*cache_or_data_depth_artifact = killed only by the 260/75-bar history gate (shallow cache, not a structure failure); gate_design_mismatch = killed only by breakout/contraction/volume gates an EARLY leader is not meant to satisfy yet; real_quality_rejection = killed by a genuine structural reason (too extended, below MA200 floor). possibly_valid_early_candidates sums the first two — names a Lens/Gatekeeper second surface could legitimately surface.*

## Candidates

| ticker | source | stage | ELS | theme | ext | lens | gk | options | alpha-board | gate root | triage |
|---|---|---|--:|---|---|---|---|---|---|---|---|
| OPTX | overlap | BREAKOUT_CONFIRMED | 64.1 | hardware | constructive | Neutral | BLOCK | — | alpha_board_cap | real_quality | **BLOCKED** |
| SATL | overlap | BREAKOUT_CONFIRMED | 61.3 | hardware | near_ema20 | Bearish but oversold | BLOCK | unusable | alpha_board_cap | real_quality | **LOW_QUALITY_NOISE** |
| RKLB | overlap | BREAKOUT_CONFIRMED | 56.0 | space_aerospace | extended | Neutral | BLOCK | ok | alpha_board_cap | real_quality | **TOO_EXTENDED** |
| FLY | overlap | BREAKOUT_CONFIRMED | 55.0 | space_aerospace | extended | Neutral | BLOCK | unusable | alpha_board_cap | gate_design_mismatch | **BLOCKED** |
| ASTS | overlap | BREAKOUT_CONFIRMED | 49.7 | space_aerospace | constructive | Neutral | BLOCK | ok | alpha_board_cap | real_quality | **BLOCKED** |
| VOYG | overlap | BREAKOUT_CONFIRMED | 48.1 | space_aerospace | constructive | Neutral | BLOCK | poor | alpha_board_cap | gate_design_mismatch | **LOW_QUALITY_NOISE** |
| OUST | overlap | BREAKOUT_CONFIRMED | 45.1 | hardware | extended | — | — | — | alpha_board_cap | real_quality | **TOO_EXTENDED** |
| ALAB | overlap | BREAKOUT_CONFIRMED | 36.3 | semiconductors | extended | — | — | — | alpha_board_cap | real_quality | **TOO_EXTENDED** |
| VELO | overlap | LATE_EXTENDED | 36.2 | hardware | extended | — | — | — | alpha_board_cap | gate_design_mismatch | **TOO_EXTENDED** |
| VPG | overlap | LATE_EXTENDED | 36.0 | hardware | extended | — | — | — | alpha_board_cap | real_quality | **TOO_EXTENDED** |
| VSH | overlap | BREAKOUT_CONFIRMED | 33.2 | semiconductors | extended | — | — | — | alpha_board_cap | real_quality | **TOO_EXTENDED** |
| ENPH | overlap | LATE_EXTENDED | 28.9 | other | extended | — | — | — | alpha_board_cap | real_quality | **TOO_EXTENDED** |
| NVTS | overlap | LATE_EXTENDED | 28.0 | semiconductors | extended | — | — | — | alpha_board_cap | real_quality | **TOO_EXTENDED** |
| AMBQ | overlap | LATE_EXTENDED | 28.0 | semiconductors | extended | — | — | — | alpha_board_cap | gate_design_mismatch | **TOO_EXTENDED** |
| ATOM | overlap | BREAKOUT_CONFIRMED | 25.7 | semiconductors | extended | — | — | — | alpha_board_cap | real_quality | **TOO_EXTENDED** |
| WOLF | overlap | LATE_EXTENDED | 19.0 | semiconductors | extended | — | — | — | alpha_board_cap | real_quality | **TOO_EXTENDED** |
| AMPX | proposed_dynamic | BREAKOUT_CONFIRMED | 77.3 | other | near_ema20 | — | — | — | alpha_board_cap | passes_a_gate | **NEEDS_LENS** |
| RGTI | theme | PULLBACK_RECLAIM | 73.2 | hardware | near_ema20 | Avoid / no edge | BLOCK | unusable | alpha_board_cap | real_quality | **BLOCKED** |
| XPO | proposed_dynamic | BREAKOUT_CONFIRMED | 72.7 | other | near_ema20 | — | — | — | alpha_board_cap | passes_a_gate | **NEEDS_LENS** |
| APLS | proposed_dynamic | PULLBACK_RECLAIM | 70.5 | biotech_healthcare | near_ema20 | — | — | — | alpha_board_cap | real_quality | **NEEDS_LENS** |
| LUNR | proposed_dynamic | PULLBACK_RECLAIM | 70.2 | space_aerospace | near_ema20 | — | — | — | alpha_board_cap | real_quality | **NEEDS_LENS** |
| DIA | proposed_dynamic | EARLY_ACCUMULATION | 70.1 | other | near_ema20 | Bullish but not buyable yet | BLOCK | unusable | alpha_board_cap | passes_a_gate | **NEEDS_GATEKEEPER** |
| COHR | proposed_dynamic | BREAKOUT_CONFIRMED | 68.7 | hardware | near_ema20 | Neutral | BLOCK | poor | alpha_board_cap | real_quality | **LOW_QUALITY_NOISE** |
| INFQ | proposed_dynamic | LOW_QUALITY_NOISE | 66.8 | hardware | near_ema20 | — | — | — | alpha_board_cap | gate_design_mismatch | **LOW_QUALITY_NOISE** |
| VIK | proposed_dynamic | BREAKOUT_CONFIRMED | 66.2 | other | near_ema20 | — | — | — | alpha_board_cap | passes_a_gate | **NEEDS_LENS** |
| PSN | proposed_dynamic | EMERGING_MOMENTUM | 66.0 | other | near_ema20 | — | — | — | alpha_board_cap | real_quality | **NEEDS_LENS** |
| DRS | proposed_dynamic | BREAKOUT_CONFIRMED | 64.3 | space_aerospace | near_ema20 | — | — | — | alpha_board_cap | gate_design_mismatch | **NEEDS_LENS** |
| LPTH | proposed_dynamic | PULLBACK_RECLAIM | 63.0 | hardware | near_ema20 | — | — | — | alpha_board_cap | gate_design_mismatch | **NEEDS_LENS** |
| BURL | proposed_dynamic | BREAKOUT_CONFIRMED | 62.6 | other | near_ema20 | — | — | — | alpha_board_cap | gate_design_mismatch | **NEEDS_LENS** |
| ANET | proposed_dynamic | LOW_QUALITY_NOISE | 62.5 | hardware | near_ema20 | Bullish but not buyable yet | WATCH | unusable | above_mcap_ceiling_80B | gate_design_mismatch | **LOW_QUALITY_NOISE** |

## Targeted refresh plan (Task 2 — design only, not executed)

DESIGN ONLY. No refresh is executed by this report. Run the commands below only with explicit operator approval.

- **build/refresh Stock Lens (PROVIDER calls — operator approval required)** — ~23 stock-lens builds (Alpaca bars + FMP profile/options per ticker)
  ```
  ./scripts/run_research_cycle.sh lens OUST ALAB VELO VPG VSH ENPH NVTS AMBQ ATOM WOLF AMPX XPO APLS LUNR INFQ VIK PSN DRS LPTH BURL RKLB RGTI ANET
  ```
- **refresh Executive Gatekeeper (cache-first; FMP earnings calendar only)** — ~6 gatekeeper rebuilds (cache-first, no per-ticker provider fan-out)
  ```
  ./scripts/run_research_cycle.sh gatekeeper-refresh --watch SATL RKLB VOYG DIA COHR ANET
  ```

## Forward maturation

Each run appends today's triage to `data/research/rs_theme_lens_triage_history.jsonl` (idempotent per date/ticker). Forward outcomes will later answer whether research-watch names outperform, too-extended names pull back, the Lens/Gatekeeper rejected correctly, and whether RS/theme triage beats the Alpha board. No future data is stored today.

