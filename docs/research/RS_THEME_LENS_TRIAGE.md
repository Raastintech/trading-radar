# RS/Theme → Lens/Gatekeeper Triage — Phase 1G.9

*Generated 2026-07-22T00:34:52.728146+00:00 · research-only · cache-only. Routing labels only — not buy/sell signals, not paper signals, not trade proposals. Does NOT modify the production universe, strategy gates, execution, or governance.*

**Verdict:** `NEED_MORE_DATA`

## Why this surface exists
Phase 1G.8 found 333/356 proposed-dynamic early leaders are killed by the Voyager/Sniper structural gates, but most rejections are cache-depth artifacts. Its own recommendation was to route RS/theme early leaders to the Stock Lens/Gatekeeper as a research-only second surface that BYPASSES those score gates. This report is that surface — diagnostic only, no gate change.

## Triage quality summary (Task 4)

| metric | value |
|---|--:|
| candidates evaluated | 30 |
| needs Lens | 11 |
| needs Gatekeeper | 1 |
| Lens-ready (both artifacts fresh) | 0 |
| too extended | 0 |
| blocked | 0 |
| research-watch | 0 |
| low-quality noise | 17 |
| not enough data | 1 |
| with options confirmation | 7 |
| in leading themes | 12 |
| killed only by Alpha-board cap | 8 |
| killed by cache/gate artifact | 8 |

**Key question:** Would routing RS/theme leaders to Lens/Gatekeeper reveal useful candidates, or just create noise?

## Gate rejection decomposition (Task 3)

- Killed by both Voyager+Sniper gates: **22** / 30 evaluable.
- Root causes: `{'cache_or_data_depth_artifact': 0, 'gate_design_mismatch': 8, 'real_quality_rejection': 14, 'unknown': 0}`
- Possibly-valid early candidates (cache-depth + gate-design only): **8**
- Bucketed reasons: `{'no_breakout': 21, 'volume_insufficient': 17, 'unknown': 17, 'no_atr_contraction': 11, 'too_extended': 8, 'ma200_missing': 6, 'insufficient_history_260': 2}`

*cache_or_data_depth_artifact = killed only by the 260/75-bar history gate (shallow cache, not a structure failure); gate_design_mismatch = killed only by breakout/contraction/volume gates an EARLY leader is not meant to satisfy yet; real_quality_rejection = killed by a genuine structural reason (too extended, below MA200 floor). possibly_valid_early_candidates sums the first two — names a Lens/Gatekeeper second surface could legitimately surface.*

## Candidates

| ticker | source | stage | ELS | theme | ext | lens | gk | options | alpha-board | gate root | triage |
|---|---|---|--:|---|---|---|---|---|---|---|---|
| WOLF | overlap | PULLBACK_RECLAIM | 52.2 | semiconductors | near_ema20 | — | — | — | alpha_board_cap | real_quality | **NEEDS_LENS** |
| VELO | overlap | PULLBACK_RECLAIM | 49.8 | other | near_ema20 | — | — | — | below_mcap_floor_300M | real_quality | **NEEDS_LENS** |
| ASTS | overlap | BROKEN | 43.0 | space_aerospace | constructive | Neutral | BLOCK | ok | alpha_board_cap | real_quality | **LOW_QUALITY_NOISE** |
| VOYG | overlap | BROKEN | 43.0 | space_aerospace | constructive | Neutral | BLOCK | poor | alpha_board_cap | real_quality | **LOW_QUALITY_NOISE** |
| FLY | overlap | BROKEN | 43.0 | space_aerospace | constructive | Neutral | BLOCK | unusable | alpha_board_cap | gate_design_mismatch | **LOW_QUALITY_NOISE** |
| RKLB | overlap | BROKEN | 43.0 | space_aerospace | constructive | Neutral | BLOCK | poor | alpha_board_cap | real_quality | **LOW_QUALITY_NOISE** |
| ENPH | overlap | BROKEN | 35.0 | other | constructive | — | — | — | alpha_board_cap | gate_design_mismatch | **LOW_QUALITY_NOISE** |
| VPG | overlap | BROKEN | 35.0 | hardware | constructive | — | — | — | alpha_board_cap | passes_a_gate | **LOW_QUALITY_NOISE** |
| NVTS | overlap | BROKEN | 35.0 | semiconductors | constructive | — | — | — | alpha_board_cap | real_quality | **LOW_QUALITY_NOISE** |
| ATOM | overlap | BROKEN | 33.0 | semiconductors | constructive | — | — | — | alpha_board_cap | gate_design_mismatch | **LOW_QUALITY_NOISE** |
| OPTX | overlap | BROKEN | 33.0 | hardware | constructive | Neutral | BLOCK | — | alpha_board_cap | gate_design_mismatch | **LOW_QUALITY_NOISE** |
| AMBQ | overlap | BROKEN | 30.0 | semiconductors | constructive | — | WATCH | — | alpha_board_cap | gate_design_mismatch | **LOW_QUALITY_NOISE** |
| ALAB | overlap | BROKEN | 28.4 | semiconductors | constructive | — | — | — | alpha_board_cap | passes_a_gate | **LOW_QUALITY_NOISE** |
| OUST | overlap | BROKEN | 27.0 | semiconductors | constructive | — | — | — | alpha_board_cap | passes_a_gate | **LOW_QUALITY_NOISE** |
| VSH | overlap | BROKEN | 27.0 | semiconductors | constructive | — | — | — | alpha_board_cap | passes_a_gate | **LOW_QUALITY_NOISE** |
| SATL | overlap | — | — | hardware | — | Bearish but oversold | BLOCK | unusable | alpha_board_cap | real_quality | **NOT_ENOUGH_DATA** |
| PACS | proposed_dynamic | PULLBACK_RECLAIM | 76.4 | biotech_healthcare | near_ema20 | — | — | — | alpha_board_cap | real_quality | **NEEDS_LENS** |
| NTAP | theme | PULLBACK_RECLAIM | 73.3 | hardware | near_ema20 | — | — | — | alpha_board_cap | real_quality | **NEEDS_LENS** |
| PSN | proposed_dynamic | EMERGING_MOMENTUM | 72.3 | other | near_ema20 | — | — | — | alpha_board_cap | real_quality | **NEEDS_LENS** |
| BIO | proposed_dynamic | BREAKOUT_CONFIRMED | 65.4 | biotech_healthcare | near_ema20 | — | — | — | alpha_board_cap | gate_design_mismatch | **NEEDS_LENS** |
| XPO | proposed_dynamic | EMERGING_MOMENTUM | 65.3 | other | near_ema20 | — | — | — | alpha_board_cap | gate_design_mismatch | **NEEDS_LENS** |
| RAL | proposed_dynamic | PULLBACK_RECLAIM | 64.1 | hardware | near_ema20 | Bullish but not buyable yet | BLOCK | unusable | alpha_board_cap | passes_a_gate | **NEEDS_LENS** |
| ILMN | proposed_dynamic | PULLBACK_RECLAIM | 63.4 | biotech_healthcare | near_ema20 | Bullish but not buyable yet | WATCH | unusable | alpha_board_cap | real_quality | **NEEDS_GATEKEEPER** |
| MT | proposed_dynamic | LOW_QUALITY_NOISE | 62.1 | other | near_ema20 | — | — | — | alpha_board_cap | gate_design_mismatch | **LOW_QUALITY_NOISE** |
| QBTS | theme | PULLBACK_RECLAIM | 61.9 | quantum | near_ema20 | — | — | — | alpha_board_cap | passes_a_gate | **NEEDS_LENS** |
| BW | proposed_dynamic | PULLBACK_RECLAIM | 61.7 | other | near_ema20 | — | — | — | alpha_board_cap | real_quality | **NEEDS_LENS** |
| DRS | proposed_dynamic | LOW_QUALITY_NOISE | 61.6 | space_aerospace | near_ema20 | — | — | — | alpha_board_cap | passes_a_gate | **LOW_QUALITY_NOISE** |
| RDW | theme | PULLBACK_RECLAIM | 59.8 | space_aerospace | near_ema20 | — | — | — | alpha_board_cap | real_quality | **NEEDS_LENS** |
| LPTH | proposed_dynamic | LOW_QUALITY_NOISE | 59.7 | hardware | near_ema20 | — | — | — | alpha_board_cap | passes_a_gate | **LOW_QUALITY_NOISE** |
| APLS | proposed_dynamic | LOW_QUALITY_NOISE | 58.9 | biotech_healthcare | near_ema20 | — | — | — | alpha_board_cap | real_quality | **LOW_QUALITY_NOISE** |

## Targeted refresh plan (Task 2 — design only, not executed)

DESIGN ONLY. No refresh is executed by this report. Run the commands below only with explicit operator approval.

- **build/refresh Stock Lens (PROVIDER calls — operator approval required)** — ~29 stock-lens builds (Alpaca bars + FMP profile/options per ticker)
  ```
  ./scripts/run_research_cycle.sh lens WOLF VELO ENPH VPG NVTS ATOM AMBQ ALAB OUST VSH PACS NTAP PSN BIO XPO MT QBTS BW DRS RDW LPTH APLS ASTS VOYG FLY RKLB OPTX SATL RAL
  ```
- **refresh Executive Gatekeeper (cache-first; FMP earnings calendar only)** — ~8 gatekeeper rebuilds (cache-first, no per-ticker provider fan-out)
  ```
  ./scripts/run_research_cycle.sh gatekeeper-refresh --watch ASTS VOYG FLY RKLB OPTX SATL RAL ILMN
  ```

## Forward maturation

Each run appends today's triage to `data/research/rs_theme_lens_triage_history.jsonl` (idempotent per date/ticker). Forward outcomes will later answer whether research-watch names outperform, too-extended names pull back, the Lens/Gatekeeper rejected correctly, and whether RS/theme triage beats the Alpha board. No future data is stored today.

