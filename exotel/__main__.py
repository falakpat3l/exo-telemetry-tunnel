"""exotel - serve StrideMate telemetry as a live dashboard, optionally tunnelled.

    python -m exotel --demo
    python -m exotel --source http://10.210.60.121 --user stridemate --password ...
    python -m exotel --source sample/session.jsonl
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
import time

from .server import Hub, new_token, serve
from .sources import build_source


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="exotel",
        description="Live gait dashboard for the StrideMate exoskeleton, "
                    "shareable over a Cloudflare quick tunnel.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  python -m exotel --demo\n"
            "      no hardware needed - synthetic gait, dropouts and all\n\n"
            "  python -m exotel --source http://10.210.60.121 \\\n"
            "                   --user stridemate --password secret --record run.jsonl\n"
            "      poll a live right leg and save the session\n\n"
            "  ./tunnel.sh --demo\n"
            "      same, plus a public https:// URL anyone can open\n"
        ),
    )
    p.add_argument("--source", default="demo",
                   help="'demo', a http(s):// StrideMate base URL, or a .jsonl recording")
    p.add_argument("--demo", action="store_const", const="demo", dest="source",
                   help="shorthand for --source demo")
    p.add_argument("--host", default="127.0.0.1",
                   help="bind address (default 127.0.0.1; the tunnel reaches it locally)")
    p.add_argument("--port", type=int, default=8787)
    p.add_argument("--rate", type=float, default=20.0,
                   help="samples per second for demo and http sources")
    p.add_argument("--token", default=None,
                   help="access token; one is generated if omitted")
    p.add_argument("--record", metavar="FILE",
                   help="append every frame to FILE as JSON lines")
    p.add_argument("--user", help="dashboard username on the exoskeleton")
    p.add_argument("--password", help="dashboard password on the exoskeleton")
    p.add_argument("--speed", type=float, default=1.0, help="replay speed multiplier")
    p.add_argument("--once", action="store_true", help="play a recording once, don't loop")
    p.add_argument("--seed", type=int, default=None, help="seed the demo generator")
    args = p.parse_args(argv)
    # Zero or negative would divide by zero (rate) or freeze replay (speed).
    if args.rate <= 0:
        p.error("--rate must be more than 0")
    if args.speed <= 0:
        p.error("--speed must be more than 0")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    token = args.token or new_token()
    hub = Hub()

    try:
        source = build_source(args)
    except (OSError, ValueError) as exc:
        print(f"error: could not open source {args.source!r}: {exc}", file=sys.stderr)
        return 2

    # Open the recording before starting the server, so a bad path is a
    # clean error message rather than a traceback with the server half up.
    recorder = None
    if args.record:
        try:
            recorder = open(args.record, "a", encoding="utf-8")
        except OSError as exc:
            print(f"error: could not open {args.record!r} for recording: {exc}", file=sys.stderr)
            return 2

    try:
        httpd = serve(hub, args.host, args.port, token)
    except OSError as exc:
        print(f"error: could not bind {args.host}:{args.port}: {exc}", file=sys.stderr)
        if recorder:
            recorder.close()
        return 2

    local = f"http://{args.host}:{args.port}/?k={token}"
    print(f"  source     {args.source}")
    print(f"  dashboard  {local}")
    if args.record:
        print(f"  recording  {args.record}")
    print("\n  The token is required on every request. Anyone with this URL can")
    print("  watch the feed, so treat it like a password. Ctrl-C to stop.\n")
    # tunnel.sh reads this line to build the public URL.
    print(f"EXOTEL_TOKEN={token}", flush=True)

    stop = threading.Event()

    def shutdown(*_):
        stop.set()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    pump = threading.Thread(
        target=_pump, args=(source, hub, recorder, stop), daemon=True
    )
    pump.start()

    try:
        while not stop.is_set():
            time.sleep(0.2)
    finally:
        stop.set()
        httpd.shutdown()
        if recorder:
            with _record_lock:
                recorder.close()
        print(f"\nstopped after {hub.frames} frames "
              f"({time.time() - hub.started:.0f}s)")
    return 0


_record_lock = threading.Lock()


def _pump(source, hub: Hub, recorder, stop: threading.Event) -> None:
    try:
        for frame in source:
            if stop.is_set():
                return
            hub.publish(frame)
            if recorder:
                # The lock stops a write landing on a file main() just closed.
                with _record_lock:
                    if not recorder.closed:
                        recorder.write(json.dumps(frame) + "\n")
                        recorder.flush()
    except Exception as exc:     # a source bug must not leave a frozen dashboard
        print(f"error: source stopped: {type(exc).__name__}: {exc}", file=sys.stderr)
    stop.set()          # a non-looping recording ran out, or the source failed


if __name__ == "__main__":
    raise SystemExit(main())
