"""Local MCP config collection (mcp-redesign §3).

Declared-file reads only: every global/project path comes from
``multica-board/config/mcp-sources.json``. Files are opened component by
component on file descriptors (``dir_fd`` + ``O_NOFOLLOW``), judged only by
``fstat``, capped at 16 MB, and never written to. Parsed containers are
reduced to entry summaries in memory; raw values only feed HMAC fingerprints
and never reach the API, SQLite, or logs.

The fingerprint key lives in the board data dir (``mcp-fingerprint.key``).
It is created with mode 0600 and rejected — never chmod'ed, overwritten, or
deleted — when any safety check fails.
"""

from __future__ import annotations

import base64
import errno
import hashlib
import hmac
import json
import logging
import os
import secrets
import stat
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

log = logging.getLogger("multica_board.mcp")

SOURCES_PATH = Path(__file__).resolve().parents[2] / "config" / "mcp-sources.json"
REPO_ROOT = Path(__file__).resolve().parents[3]

MAX_BYTES = 16 * 1024 * 1024
CACHE_TTL_SECONDS = 15
MAX_TRANSPORT_CHARS = 32

KEY_NAME = "mcp-fingerprint.key"
KEY_BYTES = 32

FP_PREFIX = b"mcp-fp-v1\0"
NAME_PREFIX = b"mcp-name-v1\0"
ROW_ID_CHARS = 20

READ_STATES = (
    "ok", "absent", "denied", "symlink", "not_regular", "too_large",
    "parse_error", "unsupported", "io_error", "platform_unsupported",
)
# Fixed Chinese detail per state (health chips / cell titles). Never the OS text.
STATE_DETAIL = {
    "ok": "已读",
    "absent": "文件不存在：确定没有",
    "denied": "没有权限，未读取",
    "symlink": "是符号链接，未跟随读取",
    "not_regular": "不是普通文件，未读取",
    "too_large": "超过 16 MB，未读取",
    "parse_error": "解析失败，未读取",
    "unsupported": "YAML 超出受限读取器能力，未读取",
    "io_error": "读取失败",
    "platform_unsupported": "平台不支持安全打开，未读取",
}
BROKEN_REASONS = ("unknown_transport", "bad_field_type", "no_target", "missing_target", "not_object")

TRANSPORT_ALIASES = {
    "stdio": "stdio",
    "local": "stdio",
    "http": "http",
    "streamable-http": "http",
    "streamable_http": "http",
    "remote": "http",
    "sse": "sse",
}
TRANSPORT_ORDER = ("stdio", "http", "sse", "unknown")

_O_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_NONBLOCK = getattr(os, "O_NONBLOCK", 0)
_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_BASE_FLAGS = os.O_RDONLY | _O_DIRECTORY | _O_CLOEXEC
_DIR_FLAGS = _BASE_FLAGS | _O_NOFOLLOW
_FILE_FLAGS = os.O_RDONLY | _O_NOFOLLOW | _O_NONBLOCK | _O_CLOEXEC
_DENIED_ERRNOS = frozenset({errno.EACCES, errno.EPERM})
_LINK_ERRNOS = frozenset({errno.ELOOP, errno.EMLINK} if hasattr(errno, "EMLINK") else {errno.ELOOP})
_NOTREG_OPEN_ERRNOS = frozenset(
    {errno.ENOTDIR, errno.ENXIO, errno.EOPNOTSUPP} & set(errno.errorcode)
)


class SourcesInvalid(Exception):
    """The mcp-sources.json declaration is unreadable or fails validation."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


# Capture the real os.open so the capability check keeps working when tests
# stub os.open to observe or swap paths.
_REAL_OPEN = os.open


def _supports_dir_fd() -> bool:
    return _REAL_OPEN in os.supports_dir_fd


def _err_state(exc: OSError) -> str:
    if exc.errno == errno.ENOENT:
        return "absent"
    if exc.errno in _NOTREG_OPEN_ERRNOS:
        return "not_regular"
    if exc.errno in _LINK_ERRNOS:
        return "symlink"
    if exc.errno in _DENIED_ERRNOS or isinstance(exc, PermissionError):
        return "denied"
    return "io_error"


# --- declaration ------------------------------------------------------------

def _valid_rel_path(path: Any) -> bool:
    if not isinstance(path, str) or not path or path.startswith("/") or "\x00" in path:
        return False
    return all(part and part not in (".", "..") for part in path.split("/"))


def _valid_file_decl(item: Any, keys: tuple[str, ...]) -> bool:
    if not isinstance(item, dict):
        return False
    for key in keys:
        if not isinstance(item.get(key), str) or not item[key]:
            return False
    return _valid_rel_path(item.get("path")) and item.get("format") in ("json", "jsonc", "toml", "yaml")


def load_sources(path: Path = SOURCES_PATH) -> dict[str, Any]:
    """Parse and validate the declaration. Raises SourcesInvalid on any defect."""
    try:
        doc = json.loads(Path(path).read_bytes().decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise SourcesInvalid("sources_unreadable") from None
    if not isinstance(doc, dict):
        raise SourcesInvalid("sources_invalid")
    tools = []
    for item in doc.get("tools") or []:
        glob = item.get("global") if isinstance(item, dict) else None
        if (
            not isinstance(item, dict)
            or not all(isinstance(item.get(k), str) and item[k] for k in ("id", "label", "short_label"))
            or not isinstance(item.get("evidence"), str)
            or not item["evidence"].strip()
            or not _valid_file_decl(glob, ("container",))
        ):
            raise SourcesInvalid("sources_invalid")
        tools.append(
            {
                "id": item["id"],
                "label": item["label"],
                "short_label": item["short_label"],
                "global": {
                    "path": glob["path"],
                    "container": glob["container"],
                    "format": glob["format"],
                },
                "evidence": item["evidence"],
            }
        )
    if not tools:
        raise SourcesInvalid("sources_invalid")
    project = []
    for item in doc.get("project") or []:
        if (
            not _valid_file_decl(item, ("location", "container"))
            or not isinstance(item.get("evidence"), str)
            or not item["evidence"].strip()
        ):
            raise SourcesInvalid("sources_invalid")
        project.append(
            {
                "path": item["path"],
                "location": item["location"],
                "container": item["container"],
                "format": item["format"],
                "evidence": item["evidence"],
            }
        )
    return {"tools": tools, "project": project}


# --- parsers ----------------------------------------------------------------

def _strip_jsonc(text: str) -> str:
    """Remove // and /* */ comments and trailing commas, outside strings only."""
    out: list[str] = []
    i, n = 0, len(text)
    in_str = False
    while i < n:
        ch = text[i]
        if in_str:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if ch == '"':
                in_str = False
            i += 1
            continue
        if ch == '"':
            in_str = True
            out.append(ch)
            i += 1
        elif ch == "/" and text[i + 1 : i + 2] == "/":
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif ch == "/" and text[i + 1 : i + 2] == "*":
            j = text.find("*/", i + 2)
            if j < 0:
                raise ValueError("unterminated comment")
            i = j + 2
        elif ch == ",":
            j = i + 1
            while j < n:
                if text[j] in " \t\r\n":
                    j += 1
                elif text[j : j + 2] == "//":
                    k = text.find("\n", j)
                    j = n if k < 0 else k
                elif text[j : j + 2] == "/*":
                    k = text.find("*/", j + 2)
                    if k < 0:
                        raise ValueError("unterminated comment")
                    j = k + 2
                else:
                    break
            if j < n and text[j] in "]}":
                i += 1  # drop the comma
            else:
                out.append(ch)
                i += 1
        else:
            out.append(ch)
            i += 1
    return "".join(out)


class _YamlUnsupported(Exception):
    pass


def _yaml_strip_comment(s: str) -> str:
    in_s = in_d = False
    i = 0
    while i < len(s):
        ch = s[i]
        if in_s:
            if ch == "'":
                if s[i + 1 : i + 2] == "'":
                    i += 1
                else:
                    in_s = False
        elif in_d:
            if ch == "\\":
                i += 1
            elif ch == '"':
                in_d = False
        else:
            if ch == "'":
                in_s = True
            elif ch == '"':
                in_d = True
            elif ch == "#" and (i == 0 or s[i - 1] in " \t"):
                return s[:i]
        i += 1
    return s


def _yaml_split_key(s: str) -> tuple[str, str] | None:
    in_s = in_d = False
    i = 0
    while i < len(s):
        ch = s[i]
        if in_s:
            if ch == "'":
                if s[i + 1 : i + 2] == "'":
                    i += 1
                else:
                    in_s = False
        elif in_d:
            if ch == "\\":
                i += 1
            elif ch == '"':
                in_d = False
        else:
            if ch == "'":
                in_s = True
            elif ch == '"':
                in_d = True
            elif ch == ":" and (i + 1 == len(s) or s[i + 1] in " \t"):
                return s[:i], s[i + 1 :].strip()
        i += 1
    return None


_YAML_DQ_ESCAPES = {'"': '"', "\\": "\\", "n": "\n", "t": "\t", "r": "\r", "0": "\0", "/": "/"}


def _yaml_dq(t: str) -> str:
    out: list[str] = []
    i = 1
    while i < len(t):
        ch = t[i]
        if ch == '"':
            if t[i + 1 :].strip():
                raise _YamlUnsupported()
            return "".join(out)
        if ch == "\\":
            nxt = t[i + 1 : i + 2]
            if nxt in _YAML_DQ_ESCAPES:
                out.append(_YAML_DQ_ESCAPES[nxt])
                i += 2
                continue
            if nxt == "x" and len(t) >= i + 4:
                try:
                    out.append(chr(int(t[i + 2 : i + 4], 16)))
                    i += 4
                    continue
                except ValueError:
                    raise _YamlUnsupported() from None
            if nxt == "u" and len(t) >= i + 6:
                try:
                    out.append(chr(int(t[i + 2 : i + 6], 16)))
                    i += 6
                    continue
                except ValueError:
                    raise _YamlUnsupported() from None
            raise _YamlUnsupported()
        out.append(ch)
        i += 1
    raise _YamlUnsupported()  # unterminated quote


def _yaml_sq(t: str) -> str:
    out: list[str] = []
    i = 1
    while i < len(t):
        ch = t[i]
        if ch == "'":
            if t[i + 1 : i + 2] == "'":
                out.append("'")
                i += 2
                continue
            if t[i + 1 :].strip():
                raise _YamlUnsupported()
            return "".join(out)
        out.append(ch)
        i += 1
    raise _YamlUnsupported()


def _yaml_flow_list(t: str) -> list[Any]:
    items: list[str] = []
    depth = 0
    in_s = in_d = False
    start = 1
    i = 1
    while i < len(t):
        ch = t[i]
        if in_s:
            if ch == "'":
                if t[i + 1 : i + 2] == "'":
                    i += 1
                else:
                    in_s = False
        elif in_d:
            if ch == "\\":
                i += 1
            elif ch == '"':
                in_d = False
        else:
            if ch == "'":
                in_s = True
            elif ch == '"':
                in_d = True
            elif ch == "[":
                depth += 1
            elif ch == "{":
                raise _YamlUnsupported()  # flow mappings are out of scope
            elif ch == "]":
                if depth == 0:
                    items.append(t[start:i])
                    if t[i + 1 :].strip():
                        raise _YamlUnsupported()
                    return [_yaml_scalar(p.strip()) for p in items if p.strip() != ""]
                depth -= 1
            elif ch == "," and depth == 0:
                items.append(t[start:i])
                start = i + 1
        i += 1
    raise _YamlUnsupported()  # unterminated flow list


def _yaml_scalar(t: str) -> Any:
    t = t.strip()
    if not t:
        raise _YamlUnsupported()
    head = t[0]
    if head in "|>&*!":
        raise _YamlUnsupported()
    if head == "[":
        return _yaml_flow_list(t)
    if head == "{":
        raise _YamlUnsupported()
    if head == '"':
        return _yaml_dq(t)
    if head == "'":
        return _yaml_sq(t)
    low = t.lower()
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    if low in ("null", "~"):
        return None
    if t.lstrip("+-").isdigit() and t not in ("+", "-"):
        try:
            return int(t)
        except ValueError:
            pass
    return t


def _yaml_key(t: str) -> str:
    t = t.strip()
    if not t or t == "<<" or t.startswith("?"):
        raise _YamlUnsupported()
    if t[0] == '"':
        return _yaml_dq(t)
    if t[0] == "'":
        return _yaml_sq(t)
    if t[0] in "&*!":
        raise _YamlUnsupported()
    return t  # keys stay literal: no bool/int coercion on names


def _yaml_tokens(text: str) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped in ("---", "...") or stripped.startswith("--- ") or stripped.startswith("... "):
            raise _YamlUnsupported()
        indent = len(raw) - len(raw.lstrip(" "))
        if raw[indent : indent + 1] == "\t":
            raise _YamlUnsupported()  # tabs are not valid YAML indentation
        content = raw[indent:]
        if content.startswith("%"):
            raise _YamlUnsupported()
        content = _yaml_strip_comment(content).rstrip()
        if not content:
            continue
        out.append((indent, content))
    return out


def _yaml_block(lines: list[tuple[int, str]], i: int, indent: int) -> tuple[Any, int]:
    content = lines[i][1]
    if content == "-" or content.startswith("- "):
        return _yaml_seq(lines, i, indent)
    return _yaml_map(lines, i, indent)


def _yaml_map(lines: list[tuple[int, str]], i: int, indent: int) -> tuple[dict, int]:
    out: dict[str, Any] = {}
    while i < len(lines):
        ind, content = lines[i]
        if ind < indent:
            break
        if ind > indent or content == "-" or content.startswith("- "):
            raise _YamlUnsupported()
        kv = _yaml_split_key(content)
        if kv is None:
            raise _YamlUnsupported()
        key = _yaml_key(kv[0])
        vraw = kv[1]
        i += 1
        if vraw == "":
            if i < len(lines) and lines[i][0] > ind:
                val, i = _yaml_block(lines, i, lines[i][0])
            else:
                val = None
        else:
            val = _yaml_scalar(vraw)
        out[key] = val
    return out, i


def _yaml_seq(lines: list[tuple[int, str]], i: int, indent: int) -> tuple[list, int]:
    out: list[Any] = []
    while i < len(lines):
        ind, content = lines[i]
        if ind != indent or not (content == "-" or content.startswith("- ")):
            break
        item = content[1:].strip()
        i += 1
        if not item:
            if i < len(lines) and lines[i][0] > ind:
                val, i = _yaml_block(lines, i, lines[i][0])
            else:
                val = None
        elif item[0] not in "[{'\"" and _yaml_split_key(item) is not None:
            raise _YamlUnsupported()  # `- key: value` nested maps are out of scope
        else:
            val = _yaml_scalar(item)
        out.append(val)
    return out, i


def yaml_mcp_container(text: str) -> Any:
    """Restricted reader: only the top-level `mcp_servers` block is parsed."""
    lines = _yaml_tokens(text)
    if not lines:
        return None
    base = lines[0][0]
    i = 0
    while i < len(lines):
        ind, content = lines[i]
        if ind != base:
            raise _YamlUnsupported()
        kv = _yaml_split_key(content)
        if kv is None:
            raise _YamlUnsupported()
        key = _yaml_key(kv[0])
        vraw = kv[1]
        i += 1
        if key == "mcp_servers":
            if vraw != "":
                return _yaml_scalar(vraw)
            if i < len(lines) and lines[i][0] > ind:
                val, _ = _yaml_block(lines, i, lines[i][0])
                return val
            return None
        # Other top-level keys are skipped without parsing their values.
        if vraw == "":
            while i < len(lines) and lines[i][0] > ind:
                i += 1
    return None


def _container_of(doc: Any, name: str) -> Any:
    if not isinstance(doc, dict):
        return _PARSE_FAILED
    value = doc.get(name)
    return value


_PARSE_FAILED = object()
_UNSUPPORTED = object()


def parse_container(data: bytes, fmt: str, container: str) -> Any:
    """bytes -> the declared container dict, _PARSE_FAILED, or _UNSUPPORTED."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return _PARSE_FAILED
    try:
        if fmt == "yaml":
            value = yaml_mcp_container(text)
        elif fmt == "toml":
            value = _container_of(tomllib.loads(text), container)
        elif fmt == "jsonc":
            value = _container_of(json.loads(_strip_jsonc(text)), container)
        else:
            value = _container_of(json.loads(text), container)
    except _YamlUnsupported:
        return _UNSUPPORTED
    except (json.JSONDecodeError, tomllib.TOMLDecodeError, ValueError, TypeError, RecursionError):
        return _PARSE_FAILED
    if value is _PARSE_FAILED:
        return _PARSE_FAILED
    if value is None:
        return {}  # container absent: readable file, zero entries
    if not isinstance(value, dict):
        return _PARSE_FAILED
    return value


# --- safe open ---------------------------------------------------------------

_READ_CACHE: dict[tuple[str, str], dict[str, Any]] = {}


def _fstat_key(st: os.stat_result) -> tuple[int, int, int, int]:
    return (st.st_dev, st.st_ino, st.st_mtime_ns, st.st_size)


def read_declared(
    base: Path,
    rel_path: str,
    container: str,
    fmt: str,
    *,
    tool: str | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Read one declared path under `base` with dir_fd/O_NOFOLLOW semantics.

    Returns {"state", "entry_count", "entries"}; entries is None unless ok.
    Entry dicts carry in-memory-only raw names and fingerprint inputs.
    """
    def result(state: str, entries: Any = None) -> dict[str, Any]:
        return {
            "state": state,
            "detail": STATE_DETAIL[state],
            "entry_count": len(entries) if isinstance(entries, list) else 0,
            "entries": entries,
        }

    if not _supports_dir_fd():
        return result("platform_unsupported")
    parts = rel_path.split("/")
    cache_key = (str(base), rel_path)
    fd = -1
    ffd = -1
    try:
        fd = os.open(str(base), _BASE_FLAGS)
        for comp in parts[:-1]:
            try:
                nxt = os.open(comp, _DIR_FLAGS, dir_fd=fd)
            except OSError as exc:
                if exc.errno == errno.ENOTDIR:
                    # Some platforms report ENOTDIR for a symlinked component:
                    # classify it without following (nothing is ever read).
                    try:
                        st = os.stat(comp, dir_fd=fd, follow_symlinks=False)
                    except OSError:
                        st = None
                    return result("symlink" if (st is not None and stat.S_ISLNK(st.st_mode)) else "not_regular")
                raise
            os.close(fd)
            fd = nxt
        ffd = os.open(parts[-1], _FILE_FLAGS, dir_fd=fd)
    except OSError as exc:
        state = _err_state(exc)
        return result(state)
    finally:
        if fd >= 0:
            os.close(fd)
    try:
        st = os.fstat(ffd)
        if not stat.S_ISREG(st.st_mode):
            return result("not_regular")
        if st.st_size > MAX_BYTES:
            return result("too_large")
        stamp = _fstat_key(st)
        hit = _READ_CACHE.get(cache_key)
        if hit is not None and hit["stamp"] == stamp and clock() - hit["at"] < CACHE_TTL_SECONDS:
            return hit["result"]
        chunks: list[bytes] = []
        total = 0
        while True:
            block = os.read(ffd, min(65536, MAX_BYTES + 1 - total))
            if not block:
                break
            chunks.append(block)
            total += len(block)
            if total > MAX_BYTES:
                return result("too_large")
        data = b"".join(chunks)
    except OSError as exc:
        return result(_err_state(exc))
    finally:
        os.close(ffd)

    parsed = parse_container(data, fmt, container)
    if parsed is _UNSUPPORTED:
        res = result("unsupported")
    elif parsed is _PARSE_FAILED:
        res = result("parse_error")
    else:
        entries = [summarize_entry(name, entry, tool) for name, entry in parsed.items()]
        res = result("ok", entries)
    _READ_CACHE.clear()  # one-slot-per-key cache: a rewrite/swap invalidates by stamp anyway
    _READ_CACHE[cache_key] = {"stamp": stamp, "at": clock(), "result": res}
    return res


def scan_sources(
    sources: dict[str, Any],
    home: Path,
    repo_root: Path,
    *,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Read every declared global and project file."""
    tools = []
    for tool in sources["tools"]:
        decl = tool["global"]
        res = read_declared(home, decl["path"], decl["container"], decl["format"], tool=tool["id"], clock=clock)
        tools.append({**tool, "declared": f"~/{decl['path']}", **res})
    project = []
    for item in sources["project"]:
        res = read_declared(repo_root, item["path"], item["container"], item["format"], clock=clock)
        project.append({**item, "declared": item["path"], **res})
    return {"tools": tools, "project": project}


# --- entry summaries ----------------------------------------------------------

_URL_KEYS = ("url", "serverUrl", "httpUrl")
_ENV_DICT_KEYS = ("env", "environment")
_ENV_NAME_KEYS = ("env_vars",)
_HEADER_KEYS = ("headers", "http_headers", "env_http_headers")


def _nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_str_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(x, str) for x in value)


def _env_names(value: Any) -> list[str]:
    if _is_str_list(value):
        return [x for x in value if x]
    if isinstance(value, dict):
        return [k for k in value if isinstance(k, str)]
    return []


def _fields_bad(entry: dict[str, Any]) -> bool:
    command = entry.get("command")
    if command is not None and not isinstance(command, str) and not _is_str_list(command):
        return True
    if entry.get("args") is not None and not _is_str_list(entry["args"]):
        return True
    if entry.get("cwd") is not None and not isinstance(entry["cwd"], str):
        return True
    for key in _URL_KEYS:
        if entry.get(key) is not None and not isinstance(entry[key], str):
            return True
    for key in _ENV_DICT_KEYS + _HEADER_KEYS:
        if entry.get(key) is not None and not isinstance(entry[key], dict):
            return True
    env_vars = entry.get("env_vars")
    if env_vars is not None and not isinstance(env_vars, dict) and not _is_str_list(env_vars):
        return True
    if entry.get("bearer_token_env_var") is not None and not isinstance(entry["bearer_token_env_var"], str):
        return True
    for key in ("disabled", "enabled"):
        if entry.get(key) is not None and not isinstance(entry[key], bool):
            return True
    return False


def _has_command(entry: dict[str, Any]) -> bool:
    command = entry.get("command")
    if _nonempty_str(command):
        return True
    return _is_str_list(command) and any(x.strip() for x in command)


def _url_values(entry: dict[str, Any]) -> dict[str, str]:
    return {k: entry[k] for k in _URL_KEYS if _nonempty_str(entry.get(k))}


def transport_enum(entry: Any, tool: str | None = None) -> str:
    """The only transport vocabulary: stdio / http / sse / unknown."""
    if not isinstance(entry, dict):
        return "unknown"
    explicit = entry.get("type")
    if explicit is None:
        explicit = entry.get("transport")
    if explicit is not None:
        if isinstance(explicit, str):
            norm = explicit.strip().lower()
            if len(norm) <= MAX_TRANSPORT_CHARS and norm in TRANSPORT_ALIASES:
                return TRANSPORT_ALIASES[norm]
        return "unknown"
    if _has_command(entry):
        return "stdio"
    urls = _url_values(entry)
    if urls:
        if tool == "gemini" and _nonempty_str(entry.get("url")):
            return "sse"
        return "http"
    return "unknown"


def norm_transport_string(value: Any) -> str:
    """Normalize a stored/CLI transport string into the enum (v1 rows included)."""
    if not isinstance(value, str):
        return "unknown"
    norm = value.strip().lower()
    if len(norm) <= MAX_TRANSPORT_CHARS and norm in TRANSPORT_ALIASES:
        return TRANSPORT_ALIASES[norm]
    return "unknown"


def _flag_count(env: dict[str, Any], names: list[str]) -> dict[str, Any]:
    count = sum(len(v) for v in env.values()) + len(names)
    return {"set": count > 0, "count": count}


def summarize_entry(name: Any, entry: Any, tool: str | None = None) -> dict[str, Any]:
    """One config entry -> summary + in-memory fingerprint inputs (§3.3)."""
    raw = name if isinstance(name, str) else str(name)
    rec: dict[str, Any] = {
        "raw": raw,
        "state": "ok",
        "broken_reason": None,
        "disabled": False,
        "transport": "unknown",
        "env": {"set": False, "count": 0},
        "headers": {"set": False, "count": 0},
        "fp_inputs": None,
    }
    if not isinstance(entry, dict):
        rec["state"] = "broken"
        rec["broken_reason"] = "not_object"
        return rec

    broken: str | None = None
    transport: str | None = None
    explicit = entry.get("type")
    if explicit is None:
        explicit = entry.get("transport")
    if explicit is not None and not isinstance(explicit, str):
        broken = "bad_field_type"
        transport = "unknown"
    elif isinstance(explicit, str):
        norm = explicit.strip().lower()
        if len(norm) <= MAX_TRANSPORT_CHARS and norm in TRANSPORT_ALIASES:
            transport = TRANSPORT_ALIASES[norm]
        else:
            broken = "unknown_transport"
            transport = "unknown"

    if broken is None and _fields_bad(entry):
        broken = "bad_field_type"

    if transport is None:
        # No explicit value: infer from command / url fields.
        if _has_command(entry):
            transport = "stdio"
        elif _url_values(entry):
            transport = "sse" if (tool == "gemini" and _nonempty_str(entry.get("url"))) else "http"
        else:
            transport = "unknown"
            if broken is None:
                broken = "no_target"
    rec["transport"] = transport

    if broken is None:
        if transport == "stdio" and not _has_command(entry):
            broken = "missing_target"
        elif transport in ("http", "sse") and not _url_values(entry):
            broken = "missing_target"

    rec["disabled"] = entry.get("disabled") is True or entry.get("enabled") is False
    if broken == "bad_field_type" or broken == "not_object":
        rec["env"] = {"set": None, "count": None}
        rec["headers"] = {"set": None, "count": None}
    else:
        env_dicts = {k: entry[k] for k in _ENV_DICT_KEYS if isinstance(entry.get(k), dict)}
        env_names = _env_names(entry.get("env_vars"))
        rec["env"] = _flag_count(env_dicts, env_names)
        header_dicts = {k: entry[k] for k in _HEADER_KEYS if isinstance(entry.get(k), dict)}
        bearer = 1 if _nonempty_str(entry.get("bearer_token_env_var")) else 0
        hcount = sum(len(v) for v in header_dicts.values()) + bearer
        rec["headers"] = {"set": hcount > 0, "count": hcount}

    if broken is None:
        if transport == "stdio":
            launch = {
                "transport": transport,
                "command": entry.get("command"),
                "args": entry.get("args") if isinstance(entry.get("args"), list) else [],
                "cwd": entry.get("cwd") if isinstance(entry.get("cwd"), str) else None,
            }
        else:
            launch = {"transport": transport, **_url_values(entry)}
        env_in: dict[str, Any] = {}
        for d in env_dicts.values():
            env_in.update(d)
        for n in env_names:
            env_in.setdefault(n, None)
        headers_in = {}
        for d in header_dicts.values():
            headers_in.update(d)
        headers_fp = {"headers": headers_in}
        if _nonempty_str(entry.get("bearer_token_env_var")):
            headers_fp["bearer_token_env_var"] = entry["bearer_token_env_var"]
        rec["fp_inputs"] = {"launch": launch, "env": env_in, "headers": headers_fp}
    else:
        rec["state"] = "broken"
        rec["broken_reason"] = broken
    return rec


def summarize_own_entry(name: Any, entry: Any) -> dict[str, Any]:
    """Own-config entries follow the same rules; tool-specific bits unset."""
    return summarize_entry(name, entry, None)


# --- fingerprints -------------------------------------------------------------

def _canon(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def fingerprints(fp_inputs: dict[str, Any], key: bytes) -> dict[str, bytes]:
    """The four HMAC-SHA256 parts; in-memory digests, never serialized."""
    parts = {
        "launch": fp_inputs["launch"],
        "env": fp_inputs["env"],
        "headers": fp_inputs["headers"],
        "all": {
            "launch": fp_inputs["launch"],
            "env": fp_inputs["env"],
            "headers": fp_inputs["headers"],
        },
    }
    return {
        part: hmac.new(key, FP_PREFIX + part.encode() + b"\0" + _canon(payload), hashlib.sha256).digest()
        for part, payload in parts.items()
    }


def row_id(salt: bytes, raw_name: str) -> str:
    digest = hmac.new(salt, NAME_PREFIX + raw_name.encode("utf-8"), hashlib.sha256).digest()
    return "m" + base64.b32encode(digest).decode("ascii").lower()[:ROW_ID_CHARS]


def assign_row_ids(salt: bytes, names: list[str]) -> dict[str, str]:
    """name -> row id. Distinct names colliding get -2/-3 suffixes (raw-name order)."""
    by_id: dict[str, list[str]] = {}
    for name in names:
        by_id.setdefault(row_id(salt, name), []).append(name)
    out: dict[str, str] = {}
    collisions = 0
    for rid, raws in by_id.items():
        unique = sorted(set(raws))
        for index, raw in enumerate(unique):
            out[raw] = rid if index == 0 else f"{rid}-{index + 1}"
            collisions += index > 0
    if collisions:
        log.warning("mcp row id collisions: %d", collisions)
    return out


# --- fingerprint key ------------------------------------------------------------

def _open_key_dir(data_dir: Path) -> int:
    """fd for the data dir, or -1. Any failure maps to key_dir_unsafe."""
    try:
        fd = os.open(str(data_dir), _BASE_FLAGS | _O_NOFOLLOW)
    except OSError:
        return -1
    return fd


def _key_open_checks(ffd: int, geteuid: Callable[[], int]) -> str | None:
    st = os.fstat(ffd)
    if not stat.S_ISREG(st.st_mode):
        return "key_not_regular"
    if st.st_uid != geteuid():
        return "key_owner"
    if st.st_mode & 0o077:
        return "key_mode"
    if st.st_nlink != 1:
        return "key_links"
    if st.st_size != KEY_BYTES:
        return "key_length"
    return None


def _read_key(data_dir: Path, geteuid: Callable[[], int]) -> dict[str, Any]:
    """Open the existing key. {"key"|None, "code"|None, "stamp"|None}."""
    dd = _open_key_dir(data_dir)
    if dd < 0:
        return {"key": None, "code": "key_dir_unsafe", "stamp": None}
    try:
        st = os.fstat(dd)
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != geteuid() or (st.st_mode & 0o022):
            return {"key": None, "code": "key_dir_unsafe", "stamp": None}
        try:
            ffd = os.open(KEY_NAME, _FILE_FLAGS, dir_fd=dd)
        except OSError as exc:
            if exc.errno == errno.ENOENT:
                return {"key": None, "code": None, "stamp": None, "missing": True}
            if exc.errno in _LINK_ERRNOS:
                return {"key": None, "code": "key_symlink", "stamp": None}
            return {"key": None, "code": "key_io_error", "stamp": None}
        try:
            bad = _key_open_checks(ffd, geteuid)
            if bad:
                return {"key": None, "code": bad, "stamp": None}
            data = os.read(ffd, KEY_BYTES + 1)
            st = os.fstat(ffd)
            if len(data) != KEY_BYTES:
                return {"key": None, "code": "key_length", "stamp": None}
            return {"key": data, "code": None, "stamp": _fstat_key(st)}
        finally:
            os.close(ffd)
    finally:
        os.close(dd)


def load_fingerprint_key(data_dir: Path, *, geteuid: Callable[[], int] = os.geteuid) -> dict[str, Any]:
    """Open or (only on ENOENT) create the 32-byte fingerprint key (§3.4).

    Rejections return {"key": None, "code": <fixed code>} and never modify,
    delete, or replace anything.
    """
    first = _read_key(data_dir, geteuid)
    if not first.get("missing"):
        return first
    # Create: tmp + fsync + hard link (fails on existing target), then re-verify.
    dd = _open_key_dir(data_dir)
    if dd < 0:
        return {"key": None, "code": "key_dir_unsafe", "stamp": None}
    try:
        st = os.fstat(dd)
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != geteuid() or (st.st_mode & 0o022):
            return {"key": None, "code": "key_dir_unsafe", "stamp": None}
        key = secrets.token_bytes(KEY_BYTES)
        tmp = f"{KEY_NAME}.tmp-{secrets.token_hex(8)}"
        try:
            tfd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_NOFOLLOW | _O_CLOEXEC, 0o600, dir_fd=dd)
        except OSError:
            return {"key": None, "code": "key_io_error", "stamp": None}
        try:
            os.write(tfd, key)
            os.fsync(tfd)
        finally:
            os.close(tfd)
        try:
            os.link(tmp, KEY_NAME, src_dir_fd=dd, dst_dir_fd=dd, follow_symlinks=False)
        except FileExistsError:
            try:
                os.unlink(tmp, dir_fd=dd)
            except OSError:
                pass
            return _read_key(data_dir, geteuid)
        except OSError:
            try:
                os.unlink(tmp, dir_fd=dd)
            except OSError:
                pass
            return {"key": None, "code": "key_io_error", "stamp": None}
        try:
            os.unlink(tmp, dir_fd=dd)
        except OSError:
            pass
    finally:
        os.close(dd)
    return _read_key(data_dir, geteuid)  # re-verify the new file end to end


# --- own-config fingerprints, in-process only (§3.5) ----------------------------

@dataclass
class OwnFingerprints:
    """Per-serve-process record of own-config fingerprints and row ids.

    agents[(workspace_id, agent_id)] = {agent_updated_at, collected_at, key_gen,
        order: [row_id aligned to the stored summary], servers: {row_id: parts}}
    library[(workspace_id, server_id)] = {id: row_id, collected_at}
    workspaces: workspace ids whose MCP data was read at least once this process.
    """

    key_gen: int = 0
    key_stamp: tuple | None = None
    agents: dict = field(default_factory=dict)
    library: dict = field(default_factory=dict)
    workspaces: set = field(default_factory=set)

    def sync_key(self, stamp: tuple | None) -> int:
        """Track the key file's fstat tuple; bump key_gen whenever it changes."""
        if stamp != self.key_stamp:
            self.key_stamp = stamp
            self.key_gen += 1
        return self.key_gen

    def record_agent(
        self,
        workspace_id: str,
        agent_id: str,
        *,
        agent_updated_at: str | None,
        collected_at: str,
        servers: dict[str, dict[str, bytes]],
        order: list[str],
    ) -> None:
        self.agents[(workspace_id, agent_id)] = {
            "agent_updated_at": agent_updated_at,
            "collected_at": collected_at,
            "key_gen": self.key_gen,
            "order": order,
            "servers": servers,
        }

    def record_library(self, workspace_id: str, rows: list[tuple[str, str]]) -> None:
        now = time.time()
        keep = {sid for sid, _ in rows}
        for key in [k for k in self.library if k[0] == workspace_id and k[1] not in keep]:
            del self.library[key]
        for sid, rid in rows:
            self.library[(workspace_id, sid)] = {"id": rid, "collected_at": now}

    def prune_agents(self, workspace_id: str, keep_ids: set[str]) -> None:
        for key in [k for k in self.agents if k[0] == workspace_id and k[1] not in keep_ids]:
            del self.agents[key]

    def agent_record(self, workspace_id: str, agent_id: str, agent_updated_at: Any, server_count: int) -> dict | None:
        rec = self.agents.get((workspace_id, agent_id))
        if rec is None or rec["key_gen"] != self.key_gen:
            return None
        if rec["agent_updated_at"] != agent_updated_at:
            return None
        if len(rec["order"]) != server_count:
            return None
        return rec


@dataclass
class McpContext:
    """Per-serve context: base dirs, process salt, own fingerprints, sources."""

    home: Path
    repo_root: Path
    data_dir: Path
    salt: bytes = field(default_factory=lambda: secrets.token_bytes(32))
    own: OwnFingerprints = field(default_factory=OwnFingerprints)
    sources_path: Path = SOURCES_PATH
    geteuid: Callable[[], int] = os.geteuid
