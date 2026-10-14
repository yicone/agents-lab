"""Loopback-only, GET-only JSON API (+ optional static frontend)."""

from __future__ import annotations

import ipaddress
import json
import logging
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

from .views import Views

log = logging.getLogger("multica_board.http")

LOOPBACK_HOSTS = {"127.0.0.1", "::1"}
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
}


def assert_loopback(host: str) -> None:
    try:
        ok = ipaddress.ip_address(host).is_loopback and host in LOOPBACK_HOSTS
    except ValueError:
        ok = False
    if not ok:
        raise ValueError(f"refusing to bind {host!r}: only 127.0.0.1 or ::1 are allowed")


class BoardServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, host: str, port: int, views: Views, static_dir: Path | None = None):
        assert_loopback(host)
        if host == "::1":
            import socket

            self.address_family = socket.AF_INET6
        self.views = views
        self.static_dir = static_dir.resolve() if static_dir else None
        super().__init__((host, port), Handler)

    @property
    def allowed_hosts(self) -> set[str]:
        port = self.server_address[1]
        return {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}


def _int(qs: dict[str, list[str]], key: str, default: int, lo: int, hi: int) -> int:
    try:
        v = int(qs.get(key, [default])[0])
    except (TypeError, ValueError):
        raise ValueError(f"{key} must be an integer")
    return max(lo, min(hi, v))


def _str(qs: dict[str, list[str]], key: str) -> str | None:
    v = qs.get(key, [None])[0]
    return v or None


def _bool(qs: dict[str, list[str]], key: str) -> bool:
    return (qs.get(key, ["0"])[0] or "").lower() in ("1", "true", "yes")


ROUTES: dict[str, Callable[[Views, dict[str, list[str]]], Any]] = {
    "/api/health": lambda v, q: {"ok": True, "read_only": True},
    "/api/meta": lambda v, q: v.meta(),
    "/api/workspaces": lambda v, q: {"workspaces": v.workspaces()},
    "/api/board": lambda v, q: v.board(workspace=_str(q, "workspace")),
    "/api/issues": lambda v, q: v.issues(
        view=_str(q, "view") or "attention",
        workspace=_str(q, "workspace"),
        q=_str(q, "q"),
        limit=_int(q, "limit", 200, 1, 1000),
        offset=_int(q, "offset", 0, 0, 1_000_000),
    ),
    "/api/agents": lambda v, q: {"agents": v.agents(workspace=_str(q, "workspace"), include_archived=_bool(q, "include_archived"))},
    "/api/runs": lambda v, q: v.runs(
        workspace=_str(q, "workspace"),
        agent=_str(q, "agent"),
        issue=_str(q, "issue"),
        status=_str(q, "status"),
        limit=_int(q, "limit", 100, 1, 500),
    ),
}


class Handler(BaseHTTPRequestHandler):
    server: BoardServer
    server_version = "multica-board"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    # --- method gate --------------------------------------------------------

    def _reject_method(self) -> None:
        self.close_connection = True  # request body is never read
        self._send_json(HTTPStatus.METHOD_NOT_ALLOWED, {"error": "read-only API: only GET and HEAD are allowed"},
                        extra={"Allow": "GET, HEAD"})

    do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_TRACE = do_CONNECT = _reject_method  # type: ignore[assignment]

    def do_HEAD(self) -> None:
        self._dispatch(head=True)

    def do_GET(self) -> None:
        self._dispatch(head=False)

    # --- core ---------------------------------------------------------------

    def _guard(self) -> bool:
        host = (self.headers.get("Host") or "").lower()
        if host not in self.server.allowed_hosts:
            self._send_json(HTTPStatus.MISDIRECTED_REQUEST, {"error": "unexpected Host header"})
            return False
        origin = self.headers.get("Origin")
        if origin and urlsplit(origin).netloc.lower() not in self.server.allowed_hosts:
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "cross-origin requests are not allowed"})
            return False
        if (self.headers.get("Sec-Fetch-Site") or "").lower() == "cross-site":
            self._send_json(HTTPStatus.FORBIDDEN, {"error": "cross-site requests are not allowed"})
            return False
        return True

    def _dispatch(self, head: bool) -> None:
        self._head = head
        if not self._guard():
            return
        parts = urlsplit(self.path)
        path = parts.path.rstrip("/") or "/"
        if path.startswith("/api/") or path == "/api":
            route = ROUTES.get(path)
            if not route:
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "unknown endpoint", "endpoints": sorted(ROUTES)})
                return
            try:
                body = route(self.server.views, parse_qs(parts.query))
            except ValueError as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            except Exception:
                log.exception("handler error on %s", path)
                self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "internal error"})
                return
            self._send_json(HTTPStatus.OK, body)
            return
        self._static(parts.path)

    def _static(self, raw_path: str) -> None:
        root = self.server.static_dir
        if root is None:
            self._send_json(HTTPStatus.OK if raw_path == "/" else HTTPStatus.NOT_FOUND,
                            {"service": "multica-board", "read_only": True, "endpoints": sorted(ROUTES)})
            return
        rel = raw_path.lstrip("/") or "index.html"
        target = (root / rel).resolve()
        if root not in target.parents and target != root:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        if target.is_dir():
            target = target / "index.html"
        if not target.is_file():
            target = root / "index.html"  # SPA fallback
            if not target.is_file():
                self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                return
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self._send(HTTPStatus.OK, target.read_bytes(), ctype)

    def _send_json(self, status: HTTPStatus, body: Any, extra: dict[str, str] | None = None) -> None:
        data = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
        self._send(status, data, "application/json; charset=utf-8", extra)

    def _send(self, status: HTTPStatus, data: bytes, ctype: str, extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in {**SECURITY_HEADERS, **(extra or {})}.items():
            self.send_header(k, v)
        self.end_headers()
        if not getattr(self, "_head", False):
            self.wfile.write(data)

    def log_message(self, fmt: str, *args: Any) -> None:
        log.info("%s %s", self.address_string(), fmt % args)
