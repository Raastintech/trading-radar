# archive/tests_archived/

Tests for execution-layer behavior that no longer applies in RESEARCH_ONLY mode.

Archived 2026-06-13 (Phase 3A). These tests verified order fill correctness,
position monitor close lifecycle, reconciler behavior, and live-capital safety.
All of that is now permanently disabled. The tests are preserved for historical
reference and in case execution is ever deliberately restored.

The current safety contract is verified by tests/unit/test_research_only_mode.py.
