"""Explicit-host read API and an opt-in loopback-only runtime action boundary."""

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


def split_values(values: list[str] | None, default: list[str]) -> list[str]:
    """Expand repeatable comma-separated CLI options without silently dropping typos."""
    result = [part.strip() for value in (values if values is not None else default)
              for part in value.split(",")]
    if not result or any(not value for value in result):
        raise ValueError("host lists must not contain empty entries")
    return list(dict.fromkeys(result))


def authority(value: str, default_port: int | None = None) -> str:
    """Validate an exact host[:port], never a URL, wildcard or credential string."""
    if not value or re.search(r"[\s/@?#\\%*]", value):
        raise ValueError(f"invalid allowed host: {value!r}")
    try:
        parsed = urlsplit("//" + value)
        hostname, port = parsed.hostname, parsed.port
        if not hostname or parsed.path or parsed.query or parsed.fragment:
            raise ValueError
        if ":" in hostname:
            hostname = str(ipaddress.IPv6Address(hostname))
            name = f"[{hostname}]"
        else:
            if not re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?", hostname):
                raise ValueError
            if any(not label or len(label) > 63 or label.startswith("-") or label.endswith("-")
                   for label in hostname.split(".")):
                raise ValueError
            name = hostname.lower()
        if value.endswith(":") or (port is not None and not 1 <= port <= 65535):
            raise ValueError
    except ValueError as exc:
        raise ValueError(f"invalid allowed host: {value!r}") from exc
    port = port if port is not None else default_port
    return f"{name}:{port}" if port is not None else name


def listen_hosts(values: list[str] | None) -> list[str]:
    hosts = split_values(values, ["127.0.0.1"])
    for host in hosts:
        # Numeric IPs avoid ambiguous DNS binding and IPv4/IPv6 selection.
        ipaddress.ip_address(host)
    return hosts


class BoardServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, host: str, port: int, views: Views, static_dir: Path | None = None, *,
                 plan_dir: Path | None = None, allowed_hosts: list[str] | None = None,
                 runtime_actions=None):
        ip = ipaddress.ip_address(host)
        configured = [authority(value) for value in (allowed_hosts or [])]
        if ip.version == 6:
            self.address_family = socket.AF_INET6
        if runtime_actions is not None and host not in LOOPBACK_HOSTS:
            raise ValueError("runtime writes require loopback-only listeners")
        self.runtime_actions = runtime_actions
        self.views = views
        from .plans import Plans
        self.plans = Plans(plan_dir) if plan_dir is not None else Plans()
        self.static_dir = static_dir.resolve() if static_dir else None
        super().__init__((host, port), Handler)
        bound_port = self.server_address[1]
        defaults = ([f"[{host}]"] if host == "::1" else ["127.0.0.1"])
        if host not in LOOPBACK_HOSTS:
            defaults.append("[::1]")
        self.allowed_hosts = {authority(value, bound_port) for value in defaults + configured}

    def server_bind(self) -> None:
        if self.address_family == socket.AF_INET6:
            self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        super().server_bind()


def bind_servers(hosts: list[str], port: int, views: Views, **kwargs: Any) -> list[BoardServer]:
    """Bind every listener before serving; on any failure release all prior sockets."""
    servers: list[BoardServer] = []
    try:
        for host in hosts:
            server = BoardServer(host, servers[0].server_port if servers else port, views, **kwargs)
            servers.append(server)
    except Exception:
        for server in servers:
            server.server_close()
        raise
    return servers


STATIC_ROUTES: dict[str, Callable[[Views], Any]] = {
    "/api/meta": lambda v: v.meta(),
    "/api/issues": lambda v: v.issues(),
    "/api/layers": lambda v: v.layers(),
    "/api/agents": lambda v: v.agents(),
    "/api/bots": lambda v: v.bots(),
    "/api/runtimes": lambda v: v.runtimes(),
    "/api/stats/failures": lambda v: v.failure_stats(),
    "/api/usage": lambda v: v.usage(),
    "/api/run-metrics": lambda v: v.run_metrics(),
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

    do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_TRACE = do_CONNECT = _reject_method  # type: ignore[assignment]

    def do_POST(self) -> None:
        self.close_connection = True
        if not self._guard():
            return
        path = urlsplit(self.path)
        actions = self.server.runtime_actions
        if actions is None or not path.path.startswith("/api/runtime-actions/"):
            self._reject_method()
            return
        from .write_adapter import WriteError
        try:
            origins = self.headers.get_all("Origin", [])
            if (not ipaddress.ip_address(self.client_address[0]).is_loopback
                    or len(origins) != 1 or origins[0] != "http://" + self.headers.get("Host", "")
                    or self.headers.get("Sec-Fetch-Site") not in (None, "same-origin")
                    or path.query):
                raise WriteError("write_origin_denied", 403)
            tokens = self.headers.get_all("X-Atlas-Write-Token", [])
            if len(tokens) > 1 or (actions.features['write_token_required'] and len(tokens) != 1):
                raise WriteError("permission_denied", 403)
            token = tokens[0] if tokens else None
            actions.authenticate(token)
            lengths = self.headers.get_all("Content-Length", [])
            if (self.headers.get("Transfer-Encoding") is not None or len(lengths) != 1
                    or not lengths[0].isdigit() or not 1 <= int(lengths[0]) <= 16384
                    or self.headers.get_all("Content-Type", []) != ["application/json"]):
                raise WriteError("invalid_request", 400)
            self.connection.settimeout(5)
            raw = self.rfile.read(int(lengths[0]))
            if len(raw) != int(lengths[0]):
                raise WriteError("invalid_request", 400)
            def unique(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError("duplicate key")
                    result[key] = value
                return result
            data = json.loads(raw, object_pairs_hook=unique)
            result = actions.handle(path.path.removeprefix("/api/runtime-actions/"), data, token)
        except WriteError as exc:
            self._error(exc.status, exc.code, exc.code)
            return
        except (ValueError, UnicodeError, TimeoutError):
            self._error(HTTPStatus.BAD_REQUEST, "invalid_request", "invalid request")
            return
        except Exception:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "action_unavailable", "action unavailable; reconcile before retry")
            return
        self._json(HTTPStatus.OK, result)

    def do_HEAD(self) -> None:
        self._dispatch(head=True)

    def do_GET(self) -> None:
        self._dispatch(head=False)

    # --- core ---------------------------------------------------------------

    def _guard(self) -> bool:
        hosts = self.headers.get_all("Host", [])
        try:
            host = authority(hosts[0]) if len(hosts) == 1 else None
        except ValueError:
            host = None
        if host not in self.server.allowed_hosts:
            self._error(HTTPStatus.MISDIRECTED_REQUEST, "bad_host", "unexpected Host header")
            return False
        origins = self.headers.get_all("Origin", [])
        if origins:
            try:
                origin = urlsplit(origins[0])
                valid = (len(origins) == 1 and not re.search(r"[\s\x00-\x1f\x7f?#]", origins[0]) and origin.scheme in ("http", "https")
                         and not (origin.path or origin.query or origin.fragment)
                         and authority(origin.netloc, 443 if origin.scheme == "https" else 80)
                         in self.server.allowed_hosts)
            except ValueError:
                valid = False
            if not valid:
                self._error(HTTPStatus.FORBIDDEN, "cross_origin", "unexpected Origin header")
                return False
        path = urlsplit(self.path).path
        is_api = path == "/api" or path.startswith("/api/")
        page_navigation = (
            not is_api
            and self.command in ("GET", "HEAD")
            and (self.headers.get("Sec-Fetch-Mode") or "").lower() == "navigate"
            and self.headers.get("Sec-Fetch-Dest", "document").lower() == "document"
        )
        if (self.headers.get("Sec-Fetch-Site") or "").lower() == "cross-site" and not page_navigation:
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
        from .plans import PlanError
        try:
            if path == "/api/runtime-actions":
                body = {"enabled": self.server.runtime_actions is not None}
            elif path == "/api/features":
                from .features import resolve_features
                actions = self.server.runtime_actions
                body = dict(actions.features) if actions else resolve_features()
            elif path == "/api/plans" or path.startswith("/api/plans/"):
                if urlsplit(self.path).query:
                    raise BadRequest("plan endpoints do not accept query parameters")
                body = self.server.plans.list() if path == "/api/plans" else self.server.plans.detail(path[len("/api/plans/"):])
            elif path == "/api/attention":
                self._attention(views)
                return
            elif path == "/api/skills/attention":
                self._skills_attention(views)
                return
            elif path == "/api/mcp":
                if urlsplit(self.path).query:
                    raise BadRequest("mcp endpoint does not accept query parameters")
                body = views.mcp()
            elif path == "/api/evidence-inventory":
                if urlsplit(self.path).query:
                    raise BadRequest("evidence inventory does not accept query parameters")
                body = views.evidence_inventory()
            elif path == "/api/skills":
                body = views.skills(self._review_days())
            elif path == "/api/skills/meta":
                body = views.skills_meta(self._review_days())
            elif path == "/api/skills/content":
                body = views.skills_content(**self._skill_content_args())
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
        except PlanError as exc:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "plan_unreadable", str(exc))
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

    def _skill_content_args(self) -> dict:
        try:
            query = parse_qs(urlsplit(self.path).query, keep_blank_values=True, max_num_fields=6)
        except ValueError as exc:
            raise BadRequest("invalid skill content query") from exc
        if set(query) - {"name", "path", "file", "tree", "source"} or any(len(v) != 1 for v in query.values()):
            raise BadRequest("invalid or repeated skill content parameter")
        args = {key: values[0] for key, values in query.items()}
        if not args.get("name") or args.get("source", "local") not in {"local", "cloud"}:
            raise BadRequest("name and valid source are required")
        if args.get("source", "local") == "local" and not args.get("path"):
            raise BadRequest("local content requires an exposed path")
        if args.get("tree", "0") not in {"0", "1"}:
            raise BadRequest("tree must be 0 or 1")
        args["tree"] = args.get("tree") == "1"
        return args

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
