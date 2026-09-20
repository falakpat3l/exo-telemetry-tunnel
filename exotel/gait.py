"""Gait metrics derived from a single distance channel.

The exoskeleton's time-of-flight sensor produces one number per leg: the
distance, in millimetres, to whatever it is pointed at. As the leg swings,
that number oscillates. Everything in this module falls out of that one
oscillation - no extra sensors, no calibration rig.

Why these particular metrics: stride time and especially *stride-time
variability* are the gait measures with the strongest clinical literature
behind them, and asymmetry between the two legs is what an assistive device
is supposed to reduce. They are also the ones you can defend in a viva,
because each is a direct consequence of the peak times and nothing else.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field


@dataclass
class GaitMetrics:
    """A snapshot of one leg's gait, or None-ish zeros before enough strides."""

    cadence_spm: float = 0.0        # STEPS per minute (two steps per stride)
    stride_time_s: float = 0.0      # mean seconds between successive peaks
    stride_cv_pct: float = 0.0      # stride-time coefficient of variation
    excursion_mm: float = 0.0       # peak-to-trough range of the last strides
    strides: int = 0                # peaks detected since start
    confident: bool = False         # enough strides for the numbers to mean much

    def as_dict(self) -> dict:
        return {
            "cadenceSpm": round(self.cadence_spm, 1),
            "strideTimeS": round(self.stride_time_s, 3),
            "strideCvPct": round(self.stride_cv_pct, 1),
            "excursionMm": round(self.excursion_mm, 1),
            "strides": self.strides,
            "confident": self.confident,
        }


class GaitTracker:
    """Detects strides in a distance signal and keeps rolling metrics.

    Peak detection is deliberately simple: a Schmitt-trigger style crossing of
    a slow-moving midline, with hysteresis proportional to the signal's own
    amplitude. Anything cleverer (FFT, template matching) would need tuning per
    user and per sensor placement, and would be harder to justify.

    Args:
        window: how many recent strides feed the rolling statistics.
        min_stride_s: strides faster than this are treated as noise. 0.25 s is
            already far quicker than a human step, so this only rejects jitter.
        min_excursion_mm: the leg must actually move this far for a crossing to
            count. Stops a stationary, noisy signal from inventing strides.
    """

    def __init__(
        self,
        window: int = 12,
        min_stride_s: float = 0.25,
        min_excursion_mm: float = 15.0,
    ) -> None:
        self.window = window
        self.min_stride_s = min_stride_s
        self.min_excursion_mm = min_excursion_mm

        self._recent: deque[tuple[float, float]] = deque(maxlen=400)  # (t, mm)
        self._stride_times: deque[float] = deque(maxlen=window)
        self._last_peak_t: float | None = None
        self._above = False
        self._strides = 0

    def reset(self) -> None:
        self._recent.clear()
        self._stride_times.clear()
        self._last_peak_t = None
        self._above = False
        self._strides = 0

    def update(self, t: float, distance_mm: float | None) -> None:
        """Feed one sample. `None` means the sensor reported no target.

        A dropout is not a zero: feeding 0 mm would fake an enormous swing.
        The sample is simply skipped, exactly as the firmware now skips it.
        """
        if distance_mm is None or not math.isfinite(distance_mm):
            return

        self._recent.append((t, distance_mm))
        if len(self._recent) < 8:
            return

        values = [v for _, v in self._recent]
        lo, hi = min(values), max(values)
        excursion = hi - lo
        if excursion < self.min_excursion_mm:
            # Leg is effectively still. Don't manufacture strides from noise.
            self._above = False
            return

        mid = (hi + lo) / 2.0
        band = excursion * 0.15          # hysteresis, scaled to the signal

        if not self._above and distance_mm > mid + band:
            self._above = True
            self._register_peak(t)
        elif self._above and distance_mm < mid - band:
            self._above = False

    def _register_peak(self, t: float) -> None:
        if self._last_peak_t is not None:
            dt = t - self._last_peak_t
            if dt < self.min_stride_s:
                return                    # too quick to be a real stride
            self._stride_times.append(dt)
        self._last_peak_t = t
        self._strides += 1

    def metrics(self) -> GaitMetrics:
        m = GaitMetrics(strides=self._strides)

        values = [v for _, v in self._recent]
        if values:
            m.excursion_mm = max(values) - min(values)

        if not self._stride_times:
            return m

        times = list(self._stride_times)
        mean = sum(times) / len(times)
        m.stride_time_s = mean
        # One stride of one leg spans two steps of the walk, and cadence is
        # conventionally reported in steps per minute - so 120, not 60.
        m.cadence_spm = 120.0 / mean if mean > 0 else 0.0

        if len(times) >= 3 and mean > 0:
            var = sum((x - mean) ** 2 for x in times) / (len(times) - 1)
            m.stride_cv_pct = 100.0 * math.sqrt(var) / mean

        # Three strides is the bare minimum for a standard deviation to exist;
        # six is where the number stops swinging wildly sample to sample.
        m.confident = len(times) >= 6
        return m


def asymmetry_pct(left: GaitMetrics, right: GaitMetrics) -> float:
    """Percentage difference in stride time between the legs.

    The symmetry index most commonly used in the literature: the difference
    over the mean, so it is direction-agnostic and scale-free. Returns 0.0
    when either side has not produced a confident stride time yet, rather
    than a number that looks meaningful and is not.
    """
    a, b = left.stride_time_s, right.stride_time_s
    if not (left.confident and right.confident) or a <= 0 or b <= 0:
        return 0.0
    return 100.0 * abs(a - b) / ((a + b) / 2.0)
