"""Token-gated HTTP server: live telemetry in, Server-Sent Events out.

Design notes worth knowing before you change anything here:

*Read-only.* This server exposes telemetry and nothing else. There is no route
that reaches back to the exoskeleton, because the whole point of the project is
to put this behind a public URL, and a public URL that can start an actuator on
someone's leg is not a thing worth building.

*Token, not password.* A Cloudflare quick tunnel is genuinely public - the
hostname is random, but random is not secret, and it is served over the open
internet. Every request must carry a token. It is generated fresh on each run
unless you pass one, and the tunnel URL is printed with it already attached.

*SSE, not polling.* One long-lived connection per viewer, pushed at the source
rate. Polling over a tunnel adds a round trip to Cloudflare's edge and back for
every sample, which is exactly what you do not want on a live gait trace.
"""

from __future__ import annotations

import http.server
import json
import queue
import secrets
import socketserver
import threading
import time
import urllib.parse
from pathlib import Path

from .gait import GaitTracker, asymmetry_pct

WEB_DIR = Path(__file__).parent / "web"
HISTORY = 240                      # samples kept for a late-joining viewer


class Hub:
    """Fans one telemetry stream out to every connected viewer."""

    def __init__(self) -> None:
        self._subscribers: set[queue.Queue] = set()
        self._lock = threading.Lock()
        self._history: list[dict] = []
        self.left = GaitTracker()
        self.right = GaitTracker()
        self.frames = 0
        self.started = time.time()

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=64)
        with self._lock:
            self._subscribers.add(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            self._subscribers.discard(q)

    @property
    def viewers(self) -> int:
        with self._lock:
            return len(self._subscribers)

    def history(self) -> list[dict]:
        with self._lock:
            return list(self._history)

    def publish(self, frame: dict) -> None:
        self.frames += 1
        t = frame.get("t", time.time())
        self.right.update(t, frame.get("rightDistance"))
        self.left.update(t, frame.get("leftDistance"))

        lm, rm = self.left.metrics(), self.right.metrics()
        frame = dict(frame)
        frame["gait"] = {
            "left": lm.as_dict(),
            "right": rm.as_dict(),
            "asymmetryPct": round(asymmetry_pct(lm, rm), 1),
        }
        frame["viewers"] = self.viewers

        with self._lock:
            self._history.append(frame)
            if len(self._history) > HISTORY:
                del self._history[: len(self._history) - HISTORY]
            dead = []
            for q in self._subscribers:
                try:
                    q.put_nowait(frame)
                except queue.Full:
                    # A viewer on a slow link. Drop the frame for them rather
                    # than stalling the source for everyone else.
                    dead.append(q)
            for q in dead:
                try:
                    q.get_nowait()
                    q.put_nowait(frame)
                except (queue.Empty, queue.Full):
                    pass


class Handler(http.server.BaseHTTPRequestHandler):
    hub: Hub = None            # type: ignore[assignment]
    token: str = ""
    server_version = "exotel"
    sys_version = ""

    def log_message(self, fmt: str, *args) -> None:
        pass                    # the CLI prints what matters; this is noise

    # -- helpers ---------------------------------------------------------
    def _authorised(self, params: dict) -> bool:
        supplied = (params.get("k") or [None])[0]
        if supplied is None:
            cookie = self.headers.get("Cookie", "")
            for part in cookie.split(";"):
                name, _, value = part.strip().partition("=")
                if name == "exotel":
                    supplied = value
                    break
        return supplied is not None and secrets.compare_digest(supplied, self.token)

    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    # -- routes ----------------------------------------------------------
    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)

        if parsed.path == "/healthz":
            self._send(200, b"ok\n", "text/plain")
            return

        if not self._authorised(params):
            self._send(
                401,
                b"Unauthorised. Append ?k=<token> to the URL.\n"
                b"The token is printed when the server starts.\n",
                "text/plain",
            )
            return

        if parsed.path in ("/", "/index.html"):
            page = (WEB_DIR / "index.html").read_bytes()
            # Remember the token so in-page fetches don't need it in every URL,
            # and so a shared link works after the query string is stripped.
            cookie = f"exotel={self.token}; Path=/; SameSite=Strict; Max-Age=86400"
            self._send(200, page, "text/html; charset=utf-8", {"Set-Cookie": cookie})
            return

        if parsed.path == "/history":
            body = json.dumps(self.hub.history()).encode()
            self._send(200, body, "application/json")
            return

        if parsed.path == "/stream":
            self._stream()
            return

        self._send(404, b"not found\n", "text/plain")

    def _stream(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        q = self.hub.subscribe()
        try:
            while True:
                try:
                    frame = q.get(timeout=15)
                    payload = f"data: {json.dumps(frame)}\n\n"
                except queue.Empty:
                    payload = ": keep-alive\n\n"   # stop the tunnel idling out
                self.wfile.write(payload.encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.hub.unsubscribe(q)


class ThreadingServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def serve(hub: Hub, host: str, port: int, token: str) -> ThreadingServer:
    """Start the server on a background thread and return it."""
    handler = type("BoundHandler", (Handler,), {"hub": hub, "token": token})
    httpd = ThreadingServer((host, port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def new_token() -> str:
    return secrets.token_urlsafe(16)
