"""Where telemetry comes from.

Three sources, one interface. Each yields dicts shaped like the StrideMate
right-leg `/data` response, so the rest of the program does not care whether
the numbers came off a real ESP32, out of a recording, or from the simulator.

    demo   - synthetic gait, so the project runs with no hardware at all
    http   - polls a live StrideMate right leg
    replay - plays back a .jsonl recording at its original speed
"""

from __future__ import annotations

import base64
import json
import math
import random
import time
import urllib.error
import urllib.request
from typing import Iterator


# Matches the firmware's assist curve, so the simulated PWM trace looks like
# the real one - including the fact that it saturates well below walking speed.
PWM_MIN_ASSIST = 80
PWM_CURVE_GAIN = 2.5
PWM_CURVE_EXPONENT = 1.5


def _assist_duty(velocity_mm_per_sample: float, ceiling: int, sensitivity: int) -> int:
    v = abs(velocity_mm_per_sample)
    if v <= sensitivity:
        return 0
    duty = PWM_MIN_ASSIST + PWM_CURVE_GAIN * (v ** PWM_CURVE_EXPONENT)
    return int(max(PWM_MIN_ASSIST, min(min(duty, 255.0), ceiling)))


class DemoSource:
    """A plausible StrideMate signal with no hardware attached.

    Models what the sensor actually sees: a roughly sinusoidal leg swing with
    a second harmonic (real gait is not a pure sine), stride-to-stride timing
    jitter, sensor noise, and occasional dropouts where the time-of-flight
    sensor loses its target. The two legs run half a cycle apart, and the left
    is given a slightly longer stride so the asymmetry metric has something to
    report.

    The dropouts are deliberate: they are the condition that used to make the
    real firmware lurch, and having them in the demo means the dashboard's
    "no target" handling is exercised every time anyone runs it.
    """

    def __init__(
        self,
        rate_hz: float = 20.0,
        seed: int | None = None,
        dropout_every_s: float = 20.0,
        dropout_len_s: float = 0.3,
    ) -> None:
        self.rate_hz = rate_hz
        self.dropout_every_s = dropout_every_s
        self.dropout_len_s = dropout_len_s
        self.rng = random.Random(seed)
        self.t0 = time.time()
        self._left_prev = 250.0
        self._right_prev = 250.0
        self._dropout_until = 0.0
        # Phase is INTEGRATED, never recomputed as t/period. Recomputing it
        # means a small change in period, multiplied by a large t, moves the
        # phase by radians - which scrambles the waveform into noise and makes
        # any stride metric read from it meaningless.
        self._phase_r = 0.0
        self._phase_l = math.pi          # contralateral: half a cycle apart
        self._jitter = 0.0               # per-stride timing jitter, in seconds
        self._last_cycle = 0.0

    def _sample(self, theta: float, prev: float):
        centre, amp = 250.0, 110.0
        mm = centre + amp * math.sin(theta) + 18.0 * math.sin(2 * theta + 0.6)
        mm += self.rng.gauss(0, 2.5)
        mm = max(45.0, min(470.0, mm))
        velocity = (mm - prev) * (20.0 / self.rate_hz)
        return mm, velocity

    def __iter__(self) -> Iterator[dict]:
        period = 1.10                   # seconds per stride, right leg
        interval = 1.0 / self.rate_hz
        battery_logic = 92.0

        prev_wall = time.time()

        while True:
            now = time.time()
            dt = min(0.25, max(1e-4, now - prev_wall))
            prev_wall = now
            t = now - self.t0

            # Stride period drifts slowly, and gets a fresh jitter once per
            # completed cycle - per stride, like a person, not per sample.
            if self._phase_r - self._last_cycle >= 2 * math.pi:
                self._last_cycle += 2 * math.pi
                self._jitter = self.rng.gauss(0, 0.022)
            p_right = period + 0.05 * math.sin(t / 7.0) + self._jitter
            p_left = p_right * 1.035    # mild asymmetry

            self._phase_r += 2 * math.pi * dt / p_right
            self._phase_l += 2 * math.pi * dt / p_left

            r_mm, r_vel = self._sample(self._phase_r, self._right_prev)
            l_mm, l_vel = self._sample(self._phase_l, self._left_prev)
            self._right_prev, self._left_prev = r_mm, l_mm

            # One dropout every dropout_every_s on average, each lasting
            # dropout_len_s. Tests turn these up to exercise the path quickly.
            if now > self._dropout_until and self.rng.random() < interval / self.dropout_every_s:
                self._dropout_until = now + self.dropout_len_s
            right_target = now >= self._dropout_until

            r_pwm = _assist_duty(r_vel, 255, 1) if right_target else 0
            l_pwm = _assist_duty(l_vel, 255, 1)

            battery_logic -= 0.0004
            yield {
                "t": now,
                "rightDistance": None if not right_target else round(r_mm, 1),
                "rightVelocity": round(r_vel, 2),
                "rightPWM": r_pwm,
                "rightPitch": round(12.0 * math.sin(self._phase_r), 2),
                "rightRoll": round(3.0 * math.sin(self._phase_r + 1.2), 2),
                "leftDistance": round(l_mm, 1),
                "leftVelocity": round(l_vel, 2),
                "leftPWM": l_pwm,
                "leftPitch": round(12.0 * math.sin(self._phase_l), 2),
                "leftRoll": round(3.0 * math.sin(self._phase_l + 1.9), 2),
                "battery": round(battery_logic, 1),
                "leftBattery": round(battery_logic - 3.0, 1),
                "thermal": round(min(0.75, 0.10 + 0.05 * math.sin(t / 30.0) + t / 4000.0), 3),
                "leftThermal": round(min(0.75, 0.08 + 0.05 * math.sin(t / 27.0) + t / 4200.0), 3),
                "motorEnabled": True,
                "assistStrength": 255,
                "sensitivity": 1,
                "leftOnline": True,
                "tofOk": True,
                "tofTarget": right_target,
                "uptimeMs": int(t * 1000),
                "source": "demo",
            }
            time.sleep(interval)


class HttpSource:
    """Polls a live StrideMate right leg over HTTP.

    Read-only by design: this reaches for `/data` and nothing else. It is
    never given the ability to enable a motor, because the whole point of the
    project is to put this feed behind a public URL, and a public URL that can
    start an actuator on someone's leg is not a thing worth building.
    """

    def __init__(
        self,
        base_url: str,
        rate_hz: float = 10.0,
        user: str | None = None,
        password: str | None = None,
        timeout: float = 1.5,
    ) -> None:
        self.url = base_url.rstrip("/") + "/data"
        self.rate_hz = rate_hz
        self.timeout = timeout
        self.headers = {"Cache-Control": "no-store"}
        if user is not None and password is not None:
            token = base64.b64encode(f"{user}:{password}".encode()).decode()
            self.headers["Authorization"] = f"Basic {token}"

    def __iter__(self) -> Iterator[dict]:
        interval = 1.0 / self.rate_hz
        while True:
            started = time.time()
            try:
                req = urllib.request.Request(self.url, headers=self.headers)
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    payload = json.loads(r.read().decode())
                payload["t"] = time.time()
                payload["source"] = "live"
                payload.setdefault("tofTarget", payload.get("tofOk", True))
                # A board reporting no target must not be read as "at 0 mm".
                if not payload.get("tofTarget", True):
                    payload["rightDistance"] = None
                yield payload
            except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
                yield {
                    "t": time.time(),
                    "source": "live",
                    "offline": True,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            elapsed = time.time() - started
            time.sleep(max(0.0, interval - elapsed))


class ReplaySource:
    """Plays a .jsonl recording back at the speed it was captured.

    Useful for showing a specific session to someone who is not in the room,
    and for re-running an odd event against a changed dashboard.
    """

    def __init__(self, path: str, loop: bool = True, speed: float = 1.0) -> None:
        self.path = path
        self.loop = loop
        self.speed = max(0.05, speed)

    def _frames(self) -> list[dict]:
        frames = []
        with open(self.path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    frames.append(json.loads(line))
        if not frames:
            raise ValueError(f"{self.path} contains no frames")
        return frames

    def __iter__(self) -> Iterator[dict]:
        frames = self._frames()
        while True:
            wall0 = time.time()
            t0 = frames[0].get("t", 0.0)
            for frame in frames:
                target = wall0 + (frame.get("t", t0) - t0) / self.speed
                delay = target - time.time()
                if delay > 0:
                    time.sleep(delay)
                out = dict(frame)
                out["t"] = time.time()
                out["source"] = "replay"
                yield out
            if not self.loop:
                return


def build_source(args) -> object:
    """Pick a source from parsed CLI arguments."""
    if args.source == "demo":
        return DemoSource(rate_hz=args.rate, seed=args.seed)
    if args.source.startswith(("http://", "https://")):
        return HttpSource(
            args.source, rate_hz=args.rate, user=args.user, password=args.password
        )
    return ReplaySource(args.source, loop=not args.once, speed=args.speed)
