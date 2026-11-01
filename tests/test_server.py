"""Security behaviour of the HTTP server: cookie flags, headers, viewer cap, idle clients."""

import http.client
import socket
import time
import unittest

from exotel import server


class ServerSecurityTest(unittest.TestCase):
    def setUp(self):
        self.hub = server.Hub()
        self.token = server.new_token()
        self.httpd = server.serve(self.hub, "127.0.0.1", 0, self.token)
        self.port = self.httpd.server_address[1]

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def get(self, path, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        c.request("GET", path, headers=headers or {})
        r = c.getresponse()
        body = r.read()
        c.close()
        return r, body

    def test_no_token_is_refused(self):
        r, _ = self.get("/")
        self.assertEqual(r.status, 401)
        r, _ = self.get("/history?k=wrong")
        self.assertEqual(r.status, 401)

    def test_page_cookie_and_headers(self):
        r, _ = self.get("/?k=" + self.token)
        self.assertEqual(r.status, 200)
        cookie = r.getheader("Set-Cookie")
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        self.assertNotIn("Secure", cookie)          # plain http on localhost
        self.assertIn("frame-ancestors 'none'", r.getheader("Content-Security-Policy"))
        self.assertEqual(r.getheader("X-Frame-Options"), "DENY")

    def test_cookie_is_secure_behind_https_tunnel(self):
        r, _ = self.get("/?k=" + self.token, {"X-Forwarded-Proto": "https"})
        self.assertIn("Secure", r.getheader("Set-Cookie"))

    def test_viewer_cap(self):
        queues = [self.hub.subscribe() for _ in range(server.MAX_VIEWERS)]
        try:
            r, _ = self.get("/stream?k=" + self.token)
            self.assertEqual(r.status, 503)
        finally:
            for q in queues:
                self.hub.unsubscribe(q)

    def test_idle_client_is_dropped(self):
        old = server.Handler.timeout
        server.Handler.timeout = 0.5
        try:
            s = socket.create_connection(("127.0.0.1", self.port), timeout=5)
            time.sleep(1.2)                    # send nothing at all
            s.settimeout(2)
            self.assertEqual(s.recv(1), b"")   # the server has closed it
            s.close()
        finally:
            server.Handler.timeout = old


if __name__ == "__main__":
    unittest.main()
