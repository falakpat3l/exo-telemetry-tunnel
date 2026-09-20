#!/usr/bin/env python3
"""End-to-end check: the server starts, the token gate holds, frames flow.

Run directly (`python scripts/smoke_test.py`) or from CI. Exits non-zero on the
first failure and prints what went wrong.

The token checks are the point. This project's whole purpose is to put a live
feed on a public URL, so "an unauthenticated request is refused" is the one
property that must never silently regress.
"""

from __future__ import annotations

import json
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from exotel.server import Hub, serve                      # noqa: E402
from exotel.sources import DemoSource                     # noqa: E402

HOST, PORT, TOKEN = "127.0.0.1", 8799, "smoke-token-do-not-use"
BASE = f"http://{HOST}:{PORT}"
failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(f"  {'PASS' if condition else 'FAIL'}  {name}" + (f" - {detail}" if detail and not condition else ""))
    if not condition:
        failures.append(name)


def get(path: str, timeout: float = 5.0):
    req = urllib.request.Request(BASE + path)
    return urllib.request.urlopen(req, timeout=timeout)


def main() -> int:
    hub = Hub()
    httpd = serve(hub, HOST, PORT, TOKEN)

    stop = threading.Event()

    def pump():
        # Dropouts turned right up: this run lasts a couple of seconds, and the
        # "a lost target is null" check below must not depend on luck.
        src = DemoSource(rate_hz=40.0, seed=1, dropout_every_s=0.2, dropout_len_s=0.1)
        for frame in src:
            if stop.is_set():
                return
            hub.publish(frame)

    threading.Thread(target=pump, daemon=True).start()
    time.sleep(0.6)                       # let a few frames accumulate

    print("exotel smoke test")
    try:
        # 1. health endpoint needs no token
        check("healthz open without a token", get("/healthz").status == 200)

        # 2. the token gate
        try:
            get("/")
            check("unauthenticated request is refused", False, "got 200")
        except urllib.error.HTTPError as e:
            check("unauthenticated request is refused", e.code == 401, f"got {e.code}")

        try:
            get("/?k=wrong-token")
            check("wrong token is refused", False, "got 200")
        except urllib.error.HTTPError as e:
            check("wrong token is refused", e.code == 401, f"got {e.code}")

        # 3. the dashboard
        r = get(f"/?k={TOKEN}")
        body = r.read().decode()
        check("dashboard served with a valid token", r.status == 200)
        check("dashboard sets the session cookie", "exotel=" in (r.headers.get("Set-Cookie") or ""))
        check("dashboard html looks complete", "</html>" in body and "EventSource" in body)

        # 4. history
        hist = json.loads(get(f"/history?k={TOKEN}").read().decode())
        check("history returns frames", len(hist) > 0, f"{len(hist)} frames")
        check("history frames carry gait metrics", bool(hist and "gait" in hist[-1]))

        # 5. the live stream
        got = []
        with get(f"/stream?k={TOKEN}", timeout=8) as stream:
            deadline = time.time() + 6
            while time.time() < deadline and len(got) < 3:
                line = stream.readline().decode()
                if line.startswith("data: "):
                    got.append(json.loads(line[6:]))
        check("stream delivers frames", len(got) >= 3, f"got {len(got)}")
        if got:
            f = got[-1]
            check("frames carry both legs", "leftDistance" in f and "rightDistance" in f)
            check("frames carry gait metrics", "gait" in f and "asymmetryPct" in f["gait"])
            check("viewer count is reported", f.get("viewers", 0) >= 1)

        # 6. a lost target is null, never a distance of zero
        seen = hist + got
        nulls = [x for x in seen if x.get("rightDistance") is None]
        check("dropouts appear as null", len(nulls) > 0, "no dropout in this window")
        check("no dropout reported as 0 mm", all(x.get("rightDistance") != 0 for x in seen))

        # 7. there is no route that can command the exoskeleton
        for path in ("/motor?on=1", "/stop", "/setPWM?value=255"):
            try:
                get(f"{path}&k={TOKEN}" if "?" in path else f"{path}?k={TOKEN}")
                check(f"no control route {path}", False, "route exists")
            except urllib.error.HTTPError as e:
                check(f"no control route {path}", e.code == 404, f"got {e.code}")

        # 8. unknown routes
        try:
            get(f"/nope?k={TOKEN}")
            check("unknown route 404s", False)
        except urllib.error.HTTPError as e:
            check("unknown route 404s", e.code == 404)

    finally:
        stop.set()
        httpd.shutdown()

    print()
    if failures:
        print(f"{len(failures)} check(s) failed: {', '.join(failures)}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
