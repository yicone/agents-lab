"""Loopback-only, GET-only JSON API (contract v0.6) plus the static web/ frontend."""

from __future__ import annotations

import ipaddress
import json
import logging
import mimetypes
import re
import socket
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, unquote, urlsplit

from .views import BadRequest, NotFound, Views

log = logging.getLogger("multica_board.http")

LOOPBACK_HOSTS = {"127.0.0.1", "::1"}
COMMON_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "Cross-Origin-Resource-Policy": "same-origin",
}
STATIC_CSP = (
    "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
    "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
)
API_CSP = "default-src 'none'; frame-ancestors 'none'"


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
            self.address_family = socket.AF_INET6
        self.views = views
        self.static_dir = static_dir.resolve() if static_dir else None
        super().__init__((host, port), Handler)
        bound_port = self.server_address[1]
        self.allowed_hosts = {f"[::1]:{bound_port}"} if host == "::1" else {f"127.0.0.1:{bound_port}"}


STATIC_ROUTES: dict[str, Callable[[Views], Any]] = {
    "/api/meta": lambda v: v.meta(),
    "/api/issues": lambda v: v.issues(),
    "/api/layers": lambda v: v.layers(),
    "/api/agents": lambda v: v.agents(),
    "/api/runtimes": lambda v: v.runtimes(),
    "/api/stats/failures": lambda v: v.failure_stats(),
    "/api/usage": lambda v: v.usage(),
}
ISSUE_RUNS = re.compile(r"^/api/issues/([0-9A-Za-z-]{1,64})/([0-9A-Za-z-]{1,64})/runs$")


def attention_format(query: str) -> str:
    """Return `json` (default) or `text`. Anything else is a client error."""
    try:
        values = parse_qs(query, keep_blank_values=True, max_num_fields=32).get("format")
    except ValueError as exc:
        raise BadRequest("format must be json or text") from exc
    if not values:
        return "json"
    kinds = {v.strip().lower() for v in values}
    if kinds == {"text"}:
        return "text"
    if kinds == {"json"}:
        return "json"
    raise BadRequest("format must be json or text")


class Handler(BaseHTTPRequestHandler):
    server: BoardServer
    server_version = "multica-board"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    # --- method gate --------------------------------------------------------

    def _reject_method(self) -> None:
        self.close_connection = True  # request body is never read
        self._error(HTTPStatus.METHOD_NOT_ALLOWED, "method_not_allowed",
                    "read-only API: only GET and HEAD are allowed", extra={"Allow": "GET, HEAD"})

    do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_TRACE = do_CONNECT = _reject_method  # type: ignore[assignment]

    def do_HEAD(self) -> None:
        self._dispatch(head=True)

    def do_GET(self) -> None:
        self._dispatch(head=False)

    # --- core ---------------------------------------------------------------

    def _guard(self) -> bool:
        host = (self.headers.get("Host") or "").lower()
        if host not in self.server.allowed_hosts:
            self._error(HTTPStatus.MISDIRECTED_REQUEST, "bad_host", "unexpected Host header")
            return False
        origin = self.headers.get("Origin")
        if origin and urlsplit(origin).netloc.lower() not in self.server.allowed_hosts:
            self._error(HTTPStatus.FORBIDDEN, "cross_origin", "cross-origin requests are not allowed")
            return False
        if (self.headers.get("Sec-Fetch-Site") or "").lower() == "cross-site":
            self._error(HTTPStatus.FORBIDDEN, "cross_origin", "cross-site requests are not allowed")
            return False
        return True

    def _dispatch(self, head: bool) -> None:
        self._head = head
        if not self._guard():
            return
        path = urlsplit(self.path).path
        if path == "/api" or path.startswith("/api/"):
            self._api(path.rstrip("/") or "/api")
        else:
            self._static(path)

    def _api(self, path: str) -> None:
        views = self.server.views
        try:
            if path == "/api/attention":
                self._attention(views)
                return
            if path == "/api/skills/attention":
                self._skills_attention(views)
                return
            if path == "/api/skills":
                body = views.skills(self._review_days())
            elif path == "/api/skills/meta":
                body = views.skills_meta(self._review_days())
            elif path in STATIC_ROUTES:
                body = STATIC_ROUTES[path](views)
            elif path == "/api/changes":
                since, kind = self._changes_args()
                body = views.changes(since, kind)
            elif m := ISSUE_RUNS.match(path):
                body = views.issue_runs(m.group(1), m.group(2))
            else:
                self._error(HTTPStatus.NOT_FOUND, "not_found", "unknown endpoint")
                return
        except BadRequest as exc:
            self._error(HTTPStatus.BAD_REQUEST, "bad_request", str(exc))
            return
        except NotFound as exc:
            self._error(HTTPStatus.NOT_FOUND, "not_found", str(exc))
            return
        except Exception:
            log.exception("handler error on %s", path)
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "internal", "internal error")
            return
        self._json(HTTPStatus.OK, body)

    def _review_days(self) -> int:
        from .skills_view import parse_review_days

        query = urlsplit(self.path).query
        if len(query) > 256:
            raise BadRequest("query string is too long")
        try:
            values = parse_qs(query, keep_blank_values=True, max_num_fields=32).get("review_days")
        except ValueError as exc:
            raise BadRequest("review_days must be 30, 90, or 180") from exc
        if not values:
            return 90
        if len(values) != 1:
            raise BadRequest("review_days must be a single value")
        try:
            return parse_review_days(values[0])
        except ValueError as exc:
            raise BadRequest(str(exc)) from exc

    def _skills_attention(self, views: Views) -> None:
        fmt = attention_format(urlsplit(self.path).query)
        days = self._review_days()
        if fmt == "text":
            data = views.skills_attention_text(days).encode()
            self._send(HTTPStatus.OK, data, "text/plain; charset=utf-8", {"Content-Security-Policy": API_CSP})
            return
        self._json(HTTPStatus.OK, views.skills_attention(days))

    def _attention(self, views: Views) -> None:
        fmt = attention_format(urlsplit(self.path).query)
        if fmt == "text":
            data = views.attention_text().encode()
            self._send(HTTPStatus.OK, data, "text/plain; charset=utf-8", {"Content-Security-Policy": API_CSP})
            return
        self._json(HTTPStatus.OK, views.attention())

    def _changes_args(self) -> tuple[str | None, str | None]:
        query = urlsplit(self.path).query
        if len(query) > 256:
            raise BadRequest("query string is too long")
        parsed = parse_qs(query, keep_blank_values=True)
        since_vals = parsed.get("since", [])
        if len(since_vals) > 1:
            raise BadRequest("since must be a single value")
        kind_vals = parsed.get("kind", [])
        if len(kind_vals) > 1:
            raise BadRequest("kind must be a single value")
        since = since_vals[0] if since_vals else None
        kind = kind_vals[0] if kind_vals else None
        return since, kind

    def _static(self, raw_path: str) -> None:
        root = self.server.static_dir
        if root is None:
            self._error(HTTPStatus.NOT_FOUND, "not_found", "no frontend directory configured")
            return
        rel = unquote(raw_path).lstrip("/") or "index.html"
        try:
            target = (root / rel).resolve()
            inside = target == root or root in target.parents
            if inside and target.is_dir():
                target = target / "index.html"
            found = inside and target.is_file()
        except (OSError, ValueError):  # e.g. embedded NUL bytes
            found = False
        if not found:
            self._error(HTTPStatus.NOT_FOUND, "not_found", "not found")
            return
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        self._send(HTTPStatus.OK, target.read_bytes(), ctype, {"Content-Security-Policy": STATIC_CSP})

    # --- responses ----------------------------------------------------------

    def _error(self, status: HTTPStatus, code: str, message: str, extra: dict[str, str] | None = None) -> None:
        self._json(status, {"error": {"code": code, "message": message}}, extra)

    def _json(self, status: HTTPStatus, body: Any, extra: dict[str, str] | None = None) -> None:
        data = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
        self._send(status, data, "application/json; charset=utf-8", {"Content-Security-Policy": API_CSP, **(extra or {})})

    def _send(self, status: HTTPStatus, data: bytes, ctype: str, extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in {**COMMON_HEADERS, **(extra or {})}.items():
            self.send_header(k, v)
        self.end_headers()
        if not getattr(self, "_head", False):
            self.wfile.write(data)

    def log_message(self, fmt: str, *args: Any) -> None:
        log.info("%s %s", self.address_string(), fmt % args)
