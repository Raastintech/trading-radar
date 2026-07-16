# RS/Theme → Lens/Gatekeeper Triage — Phase 1G.9

*Generated 2026-07-16T00:34:58.535134+00:00 · research-only · cache-only. Routing labels only — not buy/sell signals, not paper signals, not trade proposals. Does NOT modify the production universe, strategy gates, execution, or governance.*

**Verdict:** `PROMISING_RESEARCH_SURFACE`

## Why this surface exists
Phase 1G.8 found 333/356 proposed-dynamic early leaders are killed by the Voyager/Sniper structural gates, but most rejections are cache-depth artifacts. Its own recommendation was to route RS/theme early leaders to the Stock Lens/Gatekeeper as a research-only second surface that BYPASSES those score gates. This report is that surface — diagnostic only, no gate change.

## Triage quality summary (Task 4)

| metric | value |
|---|--:|
| candidates evaluated | 30 |
| needs Lens | 16 |
| needs Gatekeeper | 1 |
| Lens-ready (both artifacts fresh) | 0 |
| too extended | 0 |
| blocked | 0 |
| research-watch | 0 |
| low-quality noise | 13 |
| not enough data | 0 |
| with options confirmation | 7 |
| in leading themes | 12 |
| killed only by Alpha-board cap | 7 |
| killed by cache/gate artifact | 5 |

**Key question:** Would routing RS/theme leaders to Lens/Gatekeeper reveal useful candidates, or just create noise?

## Gate rejection decomposition (Task 3)

- Killed by both Voyager+Sniper gates: **23** / 30 evaluable.
- Root causes: `{'cache_or_data_depth_artifact': 0, 'gate_design_mismatch': 5, 'real_quality_rejection': 18, 'unknown': 0}`
- Possibly-valid early candidates (cache-depth + gate-design only): **5**
- Bucketed reasons: `{'no_atr_contraction': 19, 'too_extended': 18, 'no_breakout': 15, 'volume_insufficient': 14, 'unknown': 5, 'insufficient_history_260': 2}`

*cache_or_data_depth_artifact = killed only by the 260/75-bar history gate (shallow cache, not a structure failure); gate_design_mismatch = killed only by breakout/contraction/volume gates an EARLY leader is not meant to satisfy yet; real_quality_rejection = killed by a genuine structural reason (too extended, below MA200 floor). possibly_valid_early_candidates sums the first two — names a Lens/Gatekeeper second surface could legitimately surface.*

## Candidates

| ticker | source | stage | ELS | theme | ext | lens | gk | options | alpha-board | gate root | triage |
|---|---|---|--:|---|---|---|---|---|---|---|---|
| RKLB | overlap | PULLBACK_RECLAIM | 61.2 | space_aerospace | near_ema20 | Neutral | BLOCK | poor | alpha_board_cap | real_quality | **NEEDS_LENS** |
| VPG | overlap | PULLBACK_RECLAIM | 59.0 | hardware | near_ema20 | — | — | — | alpha_board_cap | real_quality | **NEEDS_LENS** |
| SATL | overlap | PULLBACK_RECLAIM | 58.6 | hardware | near_ema20 | Bearish but oversold | BLOCK | unusable | alpha_board_cap | real_quality | **NEEDS_LENS** |
| ASTS | overlap | PULLBACK_RECLAIM | 58.1 | space_aerospace | near_ema20 | Neutral | BLOCK | ok | alpha_board_cap | real_quality | **NEEDS_LENS** |
| OPTX | overlap | PULLBACK_RECLAIM | 56.9 | hardware | near_ema20 | Neutral | BLOCK | — | alpha_board_cap | real_quality | **NEEDS_LENS** |
| WOLF | overlap | PULLBACK_RECLAIM | 52.7 | semiconductors | near_ema20 | — | — | — | alpha_board_cap | real_quality | **NEEDS_LENS** |
| ATOM | overlap | PULLBACK_RECLAIM | 52.2 | semiconductors | near_ema20 | — | — | — | alpha_board_cap | real_quality | **NEEDS_LENS** |
| ALAB | overlap | PULLBACK_RECLAIM | 52.1 | semiconductors | near_ema20 | — | — | — | alpha_board_cap | real_quality | **NEEDS_LENS** |
| OUST | overlap | PULLBACK_RECLAIM | 51.6 | semiconductors | near_ema20 | — | — | — | alpha_board_cap | real_quality | **NEEDS_LENS** |
| ENPH | overlap | BROKEN | 51.0 | other | near_ema20 | — | — | — | alpha_board_cap | gate_design_mismatch | **LOW_QUALITY_NOISE** |
| VELO | overlap | BREAKOUT_CONFIRMED | 50.0 | other | near_ema20 | — | — | — | below_mcap_floor_300M | real_quality | **NEEDS_LENS** |
| AMBQ | overlap | PULLBACK_RECLAIM | 44.4 | semiconductors | near_ema20 | — | WATCH | — | alpha_board_cap | gate_design_mismatch | **NEEDS_LENS** |
| FLY | overlap | BROKEN | 43.0 | space_aerospace | constructive | Neutral | BLOCK | unusable | alpha_board_cap | gate_design_mismatch | **LOW_QUALITY_NOISE** |
| VOYG | overlap | BROKEN | 39.0 | space_aerospace | constructive | Neutral | BLOCK | poor | alpha_board_cap | passes_a_gate | **LOW_QUALITY_NOISE** |
| VSH | overlap | BROKEN | 31.7 | semiconductors | constructive | — | — | — | alpha_board_cap | passes_a_gate | **LOW_QUALITY_NOISE** |
| NVTS | overlap | BROKEN | 27.0 | semiconductors | constructive | — | — | — | alpha_board_cap | gate_design_mismatch | **LOW_QUALITY_NOISE** |
| NTAP | theme | PULLBACK_RECLAIM | 70.6 | hardware | near_ema20 | — | — | — | alpha_board_cap | real_quality | **NEEDS_LENS** |
| COHR | proposed_dynamic | LOW_QUALITY_NOISE | 65.5 | hardware | near_ema20 | Neutral | BLOCK | unusable | alpha_board_cap | real_quality | **LOW_QUALITY_NOISE** |
| CAH | proposed_dynamic | EARLY_ACCUMULATION | 64.6 | biotech_healthcare | near_ema20 | — | — | — | alpha_board_cap | passes_a_gate | **NEEDS_LENS** |
| DRS | proposed_dynamic | LOW_QUALITY_NOISE | 64.2 | space_aerospace | near_ema20 | — | — | — | alpha_board_cap | passes_a_gate | **LOW_QUALITY_NOISE** |
| LPTH | proposed_dynamic | LOW_QUALITY_NOISE | 63.7 | hardware | near_ema20 | — | — | — | alpha_board_cap | passes_a_gate | **LOW_QUALITY_NOISE** |
| PACS | proposed_dynamic | BREAKOUT_CONFIRMED | 60.9 | biotech_healthcare | constructive | — | — | — | alpha_board_cap | real_quality | **NEEDS_LENS** |
| APLS | proposed_dynamic | LOW_QUALITY_NOISE | 58.9 | biotech_healthcare | near_ema20 | — | — | — | alpha_board_cap | real_quality | **LOW_QUALITY_NOISE** |
| BIO | proposed_dynamic | LOW_QUALITY_NOISE | 58.5 | biotech_healthcare | near_ema20 | — | — | — | alpha_board_cap | gate_design_mismatch | **LOW_QUALITY_NOISE** |
| ILMN | proposed_dynamic | PULLBACK_RECLAIM | 58.2 | biotech_healthcare | near_ema20 | Bullish but not buyable yet | WATCH | unusable | alpha_board_cap | real_quality | **NEEDS_GATEKEEPER** |
| QBTS | theme | LOW_QUALITY_NOISE | 57.1 | quantum | near_ema20 | — | — | — | alpha_board_cap | passes_a_gate | **LOW_QUALITY_NOISE** |
| BW | proposed_dynamic | LOW_QUALITY_NOISE | 57.0 | other | near_ema20 | — | — | — | alpha_board_cap | real_quality | **LOW_QUALITY_NOISE** |
| DECK | proposed_dynamic | LOW_QUALITY_NOISE | 56.6 | other | near_ema20 | — | — | — | alpha_board_cap | passes_a_gate | **LOW_QUALITY_NOISE** |
| SMTC | RS | PULLBACK_RECLAIM | 54.7 | semiconductors | near_ema20 | — | — | — | alpha_board_cap | real_quality | **NEEDS_LENS** |
| RDW | theme | PULLBACK_RECLAIM | 53.9 | space_aerospace | near_ema20 | — | — | — | alpha_board_cap | real_quality | **NEEDS_LENS** |

## Targeted refresh plan (Task 2 — design only, not executed)

DESIGN ONLY. No refresh is executed by this report. Run the commands below only with explicit operator approval.

- **build/refresh Stock Lens (PROVIDER calls — operator approval required)** — ~29 stock-lens builds (Alpaca bars + FMP profile/options per ticker)
  ```
  ./scripts/run_research_cycle.sh lens VPG WOLF ATOM ALAB OUST ENPH VELO AMBQ VSH NVTS NTAP CAH DRS LPTH PACS APLS BIO QBTS BW DECK SMTC RDW RKLB SATL ASTS OPTX FLY VOYG COHR
  ```
- **refresh Executive Gatekeeper (cache-first; FMP earnings calendar only)** — ~8 gatekeeper rebuilds (cache-first, no per-ticker provider fan-out)
  ```
  ./scripts/run_research_cycle.sh gatekeeper-refresh --watch RKLB SATL ASTS OPTX FLY VOYG COHR ILMN
  ```

## Forward maturation

Each run appends today's triage to `data/research/rs_theme_lens_triage_history.jsonl` (idempotent per date/ticker). Forward outcomes will later answer whether research-watch names outperform, too-extended names pull back, the Lens/Gatekeeper rejected correctly, and whether RS/theme triage beats the Alpha board. No future data is stored today.

