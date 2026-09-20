"""Tests for the gait tracker.

The interesting cases are the ones that would quietly produce a plausible
wrong number: a still leg, a dropout, a signal with no oscillation. A metric
that silently invents strides is worse than one that reports nothing.
"""

import math
import unittest

from exotel.gait import GaitTracker, asymmetry_pct


def walk(tracker, period, seconds, rate=50.0, amp=110.0, centre=250.0, phase=0.0):
    """Feed a clean sinusoidal gait signal."""
    n = int(seconds * rate)
    for i in range(n):
        t = i / rate
        mm = centre + amp * math.sin(2 * math.pi * t / period + phase)
        tracker.update(t, mm)
    return tracker


class TestGaitTracker(unittest.TestCase):
    def test_recovers_known_stride_time(self):
        g = walk(GaitTracker(), period=1.10, seconds=20)
        m = g.metrics()
        self.assertTrue(m.confident, "20 s of clean gait should be confident")
        self.assertAlmostEqual(m.stride_time_s, 1.10, delta=0.05)
        self.assertAlmostEqual(m.cadence_spm, 120 / 1.10, delta=6.0)   # steps/min

    def test_clean_signal_has_near_zero_variability(self):
        g = walk(GaitTracker(), period=1.0, seconds=20)
        self.assertLess(g.metrics().stride_cv_pct, 3.0)

    def test_still_leg_produces_no_strides(self):
        g = GaitTracker()
        for i in range(600):
            g.update(i / 50.0, 250.0 + (0.4 if i % 2 else -0.4))   # noise only
        m = g.metrics()
        self.assertEqual(m.strides, 0)
        self.assertFalse(m.confident)
        self.assertEqual(m.cadence_spm, 0.0)

    def test_dropouts_do_not_fake_strides(self):
        """A None sample must be skipped, not treated as 0 mm."""
        clean = walk(GaitTracker(), period=1.0, seconds=20)
        gappy = GaitTracker()
        for i in range(int(20 * 50)):
            t = i / 50.0
            mm = 250.0 + 110.0 * math.sin(2 * math.pi * t)
            gappy.update(t, None if (i % 97) < 4 else mm)
        self.assertAlmostEqual(
            gappy.metrics().stride_time_s, clean.metrics().stride_time_s, delta=0.08
        )

    def test_excursion_tracks_amplitude(self):
        g = walk(GaitTracker(), period=1.0, seconds=10, amp=60.0)
        self.assertAlmostEqual(g.metrics().excursion_mm, 120.0, delta=12.0)

    def test_reset_clears_state(self):
        g = walk(GaitTracker(), period=1.0, seconds=10)
        self.assertGreater(g.metrics().strides, 0)
        g.reset()
        self.assertEqual(g.metrics().strides, 0)


class TestAsymmetry(unittest.TestCase):
    def test_identical_legs_are_symmetric(self):
        a = walk(GaitTracker(), period=1.0, seconds=20)
        b = walk(GaitTracker(), period=1.0, seconds=20)
        self.assertLess(asymmetry_pct(a.metrics(), b.metrics()), 2.0)

    def test_ten_percent_difference_is_detected(self):
        a = walk(GaitTracker(), period=1.00, seconds=25)
        b = walk(GaitTracker(), period=1.10, seconds=25)
        self.assertAlmostEqual(asymmetry_pct(a.metrics(), b.metrics()), 9.5, delta=3.0)

    def test_returns_zero_rather_than_a_meaningless_number(self):
        """Without confident data on both sides, report nothing, not noise."""
        a = walk(GaitTracker(), period=1.0, seconds=20)
        empty = GaitTracker()
        self.assertEqual(asymmetry_pct(a.metrics(), empty.metrics()), 0.0)


if __name__ == "__main__":
    unittest.main()
