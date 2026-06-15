"""
research/scanner_truth — Phase 1G.5 Scanner Truth Review + Missed Winner Autopsy.

Research-only. Cache-only. Reads historical OHLCV parquet, the operational
DB (decisions / veto_log / scan_results / paper_signals — read-only), and
the *latest* Alpha Discovery / Stock Lens / Gatekeeper snapshots. Makes NO
provider calls, emits NO paper signals, creates NO trade proposals, mutates
NO historical evidence, and never imports execution / governance.

Purpose: determine, from data rather than memory, whether the scanner funnel
surfaced the real market winners early enough — and where in the funnel they
dropped out. See the latest docs/research/SCANNER_TRUTH_REVIEW_YYYY_MM.md.
"""
