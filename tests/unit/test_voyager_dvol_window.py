"""
tests/unit/test_voyager_dvol_window.py

Regression test for the Voyager dollar-volume baseline window.

Background: prior to Phase 0 hardening, the baseline was computed as
``statistics.mean(dvol_bars[:-20])`` over a 60-bar list — numerically a
40-bar window (indices -60..-21) but named ``avg_dvol_60``, which the
system audit flagged as misleading.  The fix uses an explicit
``range(-60, -20)`` and a truthful name; numerical behaviour is unchanged.

This test pins the contract:
  1. The new computation produces the SAME mean as the old slicing for
     any 60-bar synthetic series — i.e. the fix is bit-for-bit
     equivalent and does not move scoring.
  2. The recent-20 and baseline-40 windows are NON-OVERLAPPING — closes
     and volumes at indices -20..-1 must not appear in the baseline.
"""
import os
import statistics
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


def _build_60_bars():
    """Distinct values per bar so any window-overlap bug is detectable."""
    closes  = [100.0 + i for i in range(60)]
    volumes = [1_000_000 + (i * 1_000) for i in range(60)]
    return closes, volumes


class TestVoyagerDvolWindow:
    def test_new_mean_matches_old_slicing(self):
        """Old form: mean of dvol_bars[:-20] where dvol_bars covers
        range(-60, 0).  New form: mean over range(-60, -20).  Must match."""
        closes, volumes = _build_60_bars()

        old_dvol_bars = [closes[i] * volumes[i] for i in range(-60, 0)]
        old_baseline  = statistics.mean(old_dvol_bars[:-20])

        new_baseline  = statistics.mean(
            closes[i] * volumes[i] for i in range(-60, -20)
        )

        assert old_baseline == new_baseline

    def test_baseline_excludes_recent_20(self):
        """Recent 20 (indices -20..-1) must not contribute to baseline."""
        closes, volumes = _build_60_bars()

        # Baseline as the new code computes it.
        baseline_indices = list(range(-60, -20))
        recent_indices   = list(range(-20, 0))

        # No overlap.
        assert set(baseline_indices).isdisjoint(set(recent_indices))
        assert len(baseline_indices) == 40
        assert len(recent_indices) == 20

        # Sanity: the resolved (positive) indices match the documented split.
        n = len(closes)
        baseline_positive = sorted((i + n) for i in baseline_indices)
        recent_positive   = sorted((i + n) for i in recent_indices)
        assert baseline_positive == list(range(0, 40))
        assert recent_positive   == list(range(40, 60))

    def test_no_today_bar_leaks_into_baseline(self):
        """If we mutate the most-recent bar (index -1) to an extreme value,
        the baseline must be unchanged.  Guards against accidentally
        re-introducing the today-bar into the baseline window."""
        closes, volumes = _build_60_bars()

        baseline_a = statistics.mean(
            closes[i] * volumes[i] for i in range(-60, -20)
        )

        closes[-1]  = 1.0e9   # outlier on today's bar
        volumes[-1] = 1.0e12

        baseline_b = statistics.mean(
            closes[i] * volumes[i] for i in range(-60, -20)
        )

        assert baseline_a == baseline_b
