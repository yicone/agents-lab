"""Remote access policy and real sockets, isolated from the installed Board."""
from __future__ import annotations

import http.client
from pathlib import Path
import socket
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from multica_board.server import BoardServer, authority, bind_servers, listen_hosts, split_values


class RemoteAccessTests(unittest.TestCase):
    def setUp(self):
        self.server = BoardServer("0.0.0.0", 0, SimpleNamespace(meta=lambda: {"ok": True}),
                                  allowed_hosts=["192.168.1.10", "atlas.tail.test:8443", "[fd7a::1]:8443",
                                                 "atlas.test:443", "atlas.test:80"])
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.port = self.server.server_port

    def tearDown(self):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()

    def request(self, host, origin=None, method="GET", extra=()):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        conn.putrequest(method, "/api/meta", skip_host=True)
        if host is not None:
            conn.putheader("Host", host)
        if origin is not None:
            conn.putheader("Origin", origin)
        for name, value in extra:
            conn.putheader(name, value)
        conn.endheaders()
        response = conn.getresponse()
        response.read()
        conn.close()
        self.assertIsNone(response.getheader("Access-Control-Allow-Origin"))
        return response.status

    def test_explicit_remote_hosts_and_origins(self):
        for host, origin in ((f"192.168.1.10:{self.port}", f"http://192.168.1.10:{self.port}"),
                             ("atlas.tail.test:8443", "https://ATLAS.TAIL.TEST:8443"),
                             ("[fd7a::1]:8443", "https://[fd7a::1]:8443"),
                             (f"127.0.0.1:{self.port}", None),
                             ("atlas.test:443", "https://atlas.test"),
                             ("atlas.test:80", "http://atlas.test")):
            for method in ("GET", "HEAD"):
                with self.subTest(host=host, method=method):
                    self.assertEqual(self.request(host, origin, method), 200)

    def test_binding_does_not_authorize_hosts(self):
        for host in (None, "0.0.0.0:" + str(self.port), "evil.test:8443", "atlas.tail.test",
                     "sub.atlas.tail.test:8443", "atlas.tail.test:443", "atlas.tail.test:8443.evil",
                     "user@atlas.tail.test:8443", "[broken", "atlas.tail.test:0"):
            with self.subTest(host=host):
                self.assertEqual(self.request(host), 421)

    def test_invalid_origins_and_cross_site_requests(self):
        host = "atlas.tail.test:8443"
        for origin in ("", "null", "\x00https://atlas.tail.test:8443", "https://evil.test:8443", "https://sub.atlas.tail.test:8443",
                       "https://atlas.tail.test:443", "//atlas.tail.test:8443",
                       "ftp://atlas.tail.test:8443", "https://user@atlas.tail.test:8443",
                       "https://atlas.tail.test:8443/path", "https://atlas.tail.test:8443?q=x",
                       "https://atlas.tail.test:8443#fragment", "https://[broken",
                       "https://atlas.tail.test:8443?", "https://atlas.tail.test:8443#",
                       "https://atlas.tail.test:8443 https://evil.test"):
            with self.subTest(origin=origin):
                self.assertEqual(self.request(host, origin), 403)
        self.assertEqual(self.request(host, extra=[("Sec-Fetch-Site", "cross-site")]), 403)

    def test_duplicate_headers_rejected(self):
        host = "atlas.tail.test:8443"
        self.assertEqual(self.request(host, extra=[("Host", host)]), 421)
        self.assertEqual(self.request(host, "https://" + host,
                                      extra=[("Origin", "https://" + host)]), 403)

    def test_remote_write_methods_stay_disabled(self):
        for method in ("POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE", "CONNECT"):
            self.assertEqual(self.request("atlas.tail.test:8443", "https://atlas.tail.test:8443", method), 405)


class ListenConfigurationTests(unittest.TestCase):
    def test_lists_and_defaults(self):
        self.assertEqual(listen_hosts(None), ["127.0.0.1"])
        self.assertEqual(listen_hosts(["127.0.0.1, ::1", "127.0.0.1"]), ["127.0.0.1", "::1"])
        self.assertEqual(split_values(["atlas.test,other.test", "third.test"], []),
                         ["atlas.test", "other.test", "third.test"])
        for values in ([""], ["127.0.0.1,"], ["localhost"], ["*"]):
            with self.assertRaises(ValueError):
                listen_hosts(values)

    def test_exact_authorities(self):
        self.assertEqual(authority("ATLAS.test", 8787), "atlas.test:8787")
        self.assertEqual(authority("[::1]", 8787), "[::1]:8787")
        self.assertEqual(authority("atlas.test:8443", 8787), "atlas.test:8443")
        for value in ("*", "*.test", "https://atlas.test", "atlas.test:", "atlas.test:65536",
                      "atlas.test:0", "a@atlas.test", "atlas.test/path", "a b", "[broken",
                      "::1", "atlas.test\\evil", "atlas.test\n", "atlas..test"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                authority(value)

    def test_ipv4_ipv6_same_port(self):
        try:
            probe = socket.socket(socket.AF_INET6)
            probe.bind(("::1", 0))
            probe.close()
        except OSError as exc:
            self.skipTest(f"IPv6 unavailable: {exc}")
        servers = bind_servers(["127.0.0.1", "::1"], 0, SimpleNamespace(meta=lambda: {}))
        threads = []
        try:
            self.assertEqual(servers[0].server_port, servers[1].server_port)
            for server in servers:
                thread = threading.Thread(target=server.serve_forever)
                thread.start()
                threads.append(thread)
            for host in ("127.0.0.1", "::1"):
                conn = http.client.HTTPConnection(host, servers[0].server_port, timeout=3)
                conn.request("GET", "/api/meta")
                response = conn.getresponse()
                response.read()
                self.assertEqual(response.status, 200)
                conn.close()
        finally:
            for server in servers:
                server.shutdown()
                server.server_close()
            for thread in threads:
                thread.join()

    def test_partial_bind_failure_releases_socket(self):
        # Duplicate bind forces the second listener to fail after the first opened.
        with mock.patch("multica_board.server.BoardServer", wraps=BoardServer) as factory:
            with self.assertRaises(OSError):
                bind_servers(["127.0.0.1", "127.0.0.1"], 0, None)
            port = factory.call_args_list[1].args[1]
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port))
