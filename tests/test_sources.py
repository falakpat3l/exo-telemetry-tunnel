"""Tests for the telemetry sources."""

import itertools
import json
import os
import tempfile
import time
import unittest

from exotel.sources import DemoSource, ReplaySource, _assist_duty


class TestAssistCurve(unittest.TestCase):
    def test_deadband(self):
        self.assertEqual(_assist_duty(0.5, 255, 1), 0)

    def test_never_below_minimum_when_active(self):
        self.assertGreaterEqual(_assist_duty(2.0, 255, 1), 80)

    def test_respects_the_ceiling(self):
        self.assertEqual(_assist_duty(500.0, 120, 1), 120)

    def test_saturates_around_seventeen_mm_per_sample(self):
        """The firmware's curve reaches 255 at |v| = 17 mm/sample.

        Pinned here because it is the surprising property of the real device:
        above this, the velocity term stops doing anything.
        """
        self.assertLess(_assist_duty(16.0, 255, 1), 255)
        self.assertEqual(_assist_duty(18.0, 255, 1), 255)


class TestDemoSource(unittest.TestCase):
    def test_emits_wellformed_frames(self):
        frames = list(itertools.islice(iter(DemoSource(rate_hz=400, seed=7)), 40))
        self.assertEqual(len(frames), 40)
        for f in frames:
            self.assertIn("t", f)
            self.assertIn("leftDistance", f)
            self.assertEqual(f["source"], "demo")
            if f["rightDistance"] is not None:
                self.assertTrue(45 <= f["rightDistance"] <= 470)
            self.assertTrue(0 <= f["rightPWM"] <= 255)

    def test_is_deterministic_for_a_given_seed(self):
        a = list(itertools.islice(iter(DemoSource(rate_hz=400, seed=3)), 12))
        b = list(itertools.islice(iter(DemoSource(rate_hz=400, seed=3)), 12))
        self.assertEqual([x["leftPWM"] for x in a], [x["leftPWM"] for x in b])

    def test_dropout_is_none_not_zero(self):
        """A lost target must never be reported as a distance of 0 mm."""
        src = DemoSource(rate_hz=400, seed=11, dropout_every_s=0.05, dropout_len_s=0.05)
        frames = list(itertools.islice(iter(src), 200))
        distances = [f["rightDistance"] for f in frames]
        self.assertIn(None, distances, "the demo should exercise dropouts")
        self.assertNotIn(0, distances)
        # tofTarget must agree with the distance being absent
        for f in frames:
            self.assertEqual(f["rightDistance"] is None, not f["tofTarget"])

    def test_no_dropouts_when_disabled(self):
        src = DemoSource(rate_hz=400, seed=5, dropout_every_s=10**9)
        frames = list(itertools.islice(iter(src), 60))
        self.assertNotIn(None, [f["rightDistance"] for f in frames])


class TestReplaySource(unittest.TestCase):
    def test_round_trips_a_recording(self):
        fd, path = tempfile.mkstemp(suffix=".jsonl")
        try:
            with os.fdopen(fd, "w") as fh:
                for i in range(5):
                    fh.write(json.dumps({"t": i * 0.01, "leftDistance": 100 + i}) + "\n")
            frames = list(itertools.islice(iter(ReplaySource(path, loop=False, speed=50)), 5))
            self.assertEqual([f["leftDistance"] for f in frames], [100, 101, 102, 103, 104])
            self.assertTrue(all(f["source"] == "replay" for f in frames))
        finally:
            os.unlink(path)

    def test_empty_recording_is_an_error(self):
        fd, path = tempfile.mkstemp(suffix=".jsonl")
        os.close(fd)
        try:
            with self.assertRaises(ValueError):
                list(iter(ReplaySource(path, loop=False)))
        finally:
            os.unlink(path)

    def test_missing_file_fails_when_opened_not_later(self):
        """The CLI catches this before the server starts, so it must raise here."""
        with self.assertRaises(OSError):
            ReplaySource("/no/such/recording.jsonl")

    def test_skips_malformed_lines(self):
        """A recording cut off mid-line (Ctrl-C, full disk) still plays."""
        fd, path = tempfile.mkstemp(suffix=".jsonl")
        try:
            with os.fdopen(fd, "w") as fh:
                fh.write(json.dumps({"t": 0.0, "leftDistance": 100}) + "\n")
                fh.write("not json at all\n")
                fh.write("[1, 2, 3]\n")
                fh.write(json.dumps({"t": "soon", "leftDistance": 101}) + "\n")
                fh.write(json.dumps({"t": 0.02, "leftDistance": 102}) + "\n")
                fh.write('{"t": 0.03, "leftDist')            # truncated last line
            src = ReplaySource(path, loop=False, speed=50)
            frames = list(iter(src))
            self.assertEqual([f["leftDistance"] for f in frames], [100, 101, 102])
            self.assertEqual(src.skipped, 3)
        finally:
            os.unlink(path)

    def test_bad_first_time_does_not_stall_playback(self):
        """Real recordings hold clock times; a first line without one must not mean t0 = 0."""
        fd, path = tempfile.mkstemp(suffix=".jsonl")
        try:
            with os.fdopen(fd, "w") as fh:
                fh.write(json.dumps({"t": None, "leftDistance": 100}) + "\n")
                fh.write(json.dumps({"t": 1760000000.0, "leftDistance": 101}) + "\n")
                fh.write(json.dumps({"t": 1760000000.02, "leftDistance": 102}) + "\n")
            src = ReplaySource(path, loop=False, speed=50)
            started = time.time()
            frames = list(iter(src))
            self.assertEqual([f["leftDistance"] for f in frames], [100, 101, 102])
            self.assertLess(time.time() - started, 2)
        finally:
            os.unlink(path)


class TestServerFrames(unittest.TestCase):
    def test_nan_becomes_null_so_the_browser_can_parse_it(self):
        from exotel.server import Hub
        hub = Hub()
        hub.publish({"t": "bad", "rightDistance": float("nan"), "leftDistance": 200.0,
                     "thermal": float("inf")})
        frame = hub.history()[-1]
        self.assertIsNone(frame["rightDistance"])
        self.assertIsNone(frame["thermal"])
        self.assertIsInstance(frame["t"], float)
        json.loads(json.dumps(frame, allow_nan=False))   # strict JSON, no NaN


if __name__ == "__main__":
    unittest.main()
