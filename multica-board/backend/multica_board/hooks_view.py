"""Read-only hooks inventory for the capability page.

Sources are Cursor, Codex user hooks, Grok CLI, and Hermes plugin events.
Hermes shell is read only to tell an empty list from a failed read. Grok Bot
and Multica are not sources. Command text never leaves this module.
"""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

HOOK_SOURCES = (
    ("cursor", "Cursor"),
    ("codex_user", "Codex 用户 hooks"),
    ("grok_cli", "Grok CLI"),
    ("hermes_plugin", "Hermes 插件事件"),
)

MAX_BYTES = 1_000_000
GROK_TIMEOUT_S = 8
GROK_CACHE_S = 15
_EVENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_.:-]{0,80}$")
_ABS = re.compile(r"(?<![\w.~])(/[^\s\"';|&<>]+)")
_GROK_ERROR = re.compile(r"^hook hooks:([A-Za-z0-9_.:()-]+)\[(\d+)\]")

_grok_cache: dict[str, Any] = {"at": 0.0, "payload": None, "error": None}

Inspect = Callable[[], tuple[dict[str, Any] | None, str | None]]


def collect_hooks(
    home: Path,
    inspect: Inspect | None = None,
    now: float | None = None,
    exists: Callable[[str], bool] | None = None,
) -> dict[str, Any]:
    home = Path(home)
    file_exists = exists or os.path.exists
    items: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    for source_id, label, reader in (
        ("cursor", "Cursor", lambda root: _read_cursor(root, file_exists)),
        ("codex_user", "Codex 用户 hooks", lambda root: _read_codex(root, file_exists)),
        ("grok_cli", "Grok CLI", lambda root: _read_grok(root, inspect, now, file_exists)),
        ("hermes_plugin", "Hermes 插件事件", _read_hermes_plugins),
    ):
        try:
            block, error = reader(home)
        except Exception:
            block, error = [], _error("unreadable", "这个来源没读到")
        source_items = [_public_item(source_id, label, item) for item in block]
        items.extend(source_items)
        code = error.get("code") if error else ""
        if code == "invalid":
            state = "invalid"
        elif code in {"unreadable", "missing", "timeout", "failed"} and not source_items:
            state = "unavailable"
        else:
            state = "collected"
        sources.append({
            "id": source_id,
            "label": label,
            "collection_state": state,
            "count": len(source_items),
            "error": error,
        })
    shell_state, shell_count = _read_hermes_shell(home)
    return {
        "ok": True,
        "hooks_schema": 1,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now if now is not None else time.time())),
        "sources": sources,
        "items": items,
        "hermes_shell": {"collection_state": shell_state, "count": shell_count, "error": None if shell_state == "collected" else _error("unreadable", "Hermes shell 没读到")},
    }


def _public_item(source_id: str, label: str, item: dict[str, Any]) -> dict[str, Any]:
    event = str(item.get("event") or "")
    index = int(item.get("index") or 0)
    plugin = item.get("plugin") or ""
    identity = str(item.get("id") or (f"{source_id}:{plugin}:{event}:{index}" if plugin else f"{source_id}:{event}:{index}"))
    return {
        "id": identity,
        "name": event,
        "event": event,
        "plugin": plugin,
        "source_id": source_id,
        "source_label": label,
        "declared": "present",
        "reachable": item.get("reachable") if item.get("reachable") in {"present", "missing", "unknown"} else "unknown",
        "observed": "unknown",
    }


def _error(code: str, message: str, count: int | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"code": code, "message": message}
    if count is not None:
        body["count"] = count
    return body


def _read_file(path: Path) -> tuple[str | None, str]:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None, "absent"
    except PermissionError:
        return None, "denied"
    except OSError:
        return None, "io"
    if stat.S_ISLNK(info.st_mode):
        return None, "symlink"
    if not stat.S_ISREG(info.st_mode):
        return None, "not_file"
    if info.st_size > MAX_BYTES:
        return None, "too_large"
    try:
        return path.read_text(encoding="utf-8"), "ok"
    except PermissionError:
        return None, "denied"
    except OSError:
        return None, "io"


def _unavailable(reason: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    return [], _error("unreadable", "这个来源没读到" if reason != "invalid" else "这个来源的内容读不懂")


def path_reach(command: str, exists: Callable[[str], bool], *, compatibility: str | None = None, load_error: bool = False) -> str:
    """Return yes / no / unknown. ``enabled`` and ``trusted_hash`` are not arguments."""
    if load_error:
        return "no"
    if compatibility not in (None, "", "enabled"):
        return "unknown"
    paths = []
    for match in _ABS.finditer(command or ""):
        path = match.group(1).rstrip("),")
        if path.startswith("//") or len(path) < 2:
            continue
        paths.append(path)
    if not paths:
        return "unknown"
    return "yes" if all(exists(path) for path in paths) else "no"


def _mark(command: str, exists: Callable[[str], bool], *, compatibility: str | None = None, load_error: bool = False) -> str:
    return {"yes": "present", "no": "missing"}.get(
        path_reach(command, exists, compatibility=compatibility, load_error=load_error),
        "unknown",
    )


def _error_ref(text: str) -> tuple[str, int, str] | None:
    match = _GROK_ERROR.match(text.strip())
    if not match:
        return None
    rest = text[match.end():]
    path = ""
    marker = " in "
    if marker in rest:
        path = rest.split(marker, 1)[1].split(":", 1)[0].strip()
    return match.group(1), int(match.group(2)), path


def _read_cursor(home: Path, exists: Callable[[str], bool]) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    text, reason = _read_file(home / ".cursor" / "hooks.json")
    if reason == "absent":
        return [], None
    if reason != "ok" or text is None:
        return _unavailable(reason)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return [], _error("invalid", "这个来源的内容读不懂")
    hooks = data.get("hooks") if isinstance(data, dict) else None
    if not isinstance(hooks, dict):
        return [], _error("invalid", "这个来源的内容读不懂")
    items = []
    for event, entries in hooks.items():
        if not isinstance(event, str) or not isinstance(entries, list):
            continue
        for index, entry in enumerate(entries):
            command = entry.get("command") if isinstance(entry, dict) else ""
            items.append({"event": event, "index": index, "reachable": _mark(command if isinstance(command, str) else "", exists)})
    return items, None


def _read_codex(home: Path, exists: Callable[[str], bool]) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    text, reason = _read_file(home / ".codex" / "hooks.json")
    if reason == "absent":
        return [], None
    if reason != "ok" or text is None:
        return _unavailable(reason)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return [], _error("invalid", "这个来源的内容读不懂")
    groups = data.get("hooks") if isinstance(data, dict) else None
    if not isinstance(groups, dict):
        return [], _error("invalid", "这个来源的内容读不懂")
    items = []
    index = 0
    for event, entries in groups.items():
        if not isinstance(event, str) or not isinstance(entries, list):
            continue
        for entry in entries:
            nested = entry.get("hooks") if isinstance(entry, dict) else None
            commands = nested if isinstance(nested, list) else [entry]
            for command_entry in commands:
                command = command_entry.get("command") if isinstance(command_entry, dict) else ""
                items.append({"event": event, "index": index, "reachable": _mark(command if isinstance(command, str) else "", exists)})
                index += 1
    return items, None


def _read_grok(
    home: Path,
    inspect: Inspect | None,
    now: float | None,
    exists: Callable[[str], bool],
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    del home  # Grok CLI reports its own discovery; the Cursor file is not merged in.
    payload, failure = _cached_grok(inspect, now)
    if failure:
        message = {
            "missing": "本机没有 Grok CLI，未读取",
            "timeout": "Grok CLI 读取超时",
            "invalid": "Grok CLI 没有返回清单",
        }.get(failure, "Grok CLI 没有返回清单")
        return [], _error(failure, message)
    hooks = payload.get("hooks") if isinstance(payload, dict) else None
    if not isinstance(hooks, list):
        return [], _error("invalid", "Grok CLI 没有返回清单")
    raw_errors = payload.get("hookLoadErrors")
    error_refs = [_error_ref(str(text)) for text in raw_errors] if isinstance(raw_errors, list) else []
    matched: set[int] = set()
    items = []
    local_index: dict[tuple[str, str], int] = {}
    for index, hook in enumerate(hooks):
        if not isinstance(hook, dict):
            continue
        event = hook.get("event") if isinstance(hook.get("event"), str) else ""
        source = hook.get("source") if isinstance(hook.get("source"), dict) else {}
        plugin = source.get("plugin_name") if isinstance(source.get("plugin_name"), str) else ""
        source_path = source.get("path") if isinstance(source.get("path"), str) else ""
        shown = event or plugin or "hook"
        key = (source_path, shown)
        local = local_index.get(key, 0)
        local_index[key] = local + 1
        load_error = False
        if source_path.startswith("/") and source_path != "/":
            for err_index, ref in enumerate(error_refs):
                if ref and ref[0] == shown and ref[1] == local and ref[2] == source_path:
                    load_error = True
                    matched.add(err_index)
        compatibility = hook.get("compatibilityStatus")
        compat = compatibility if isinstance(compatibility, str) else None
        target = hook.get("target") if isinstance(hook.get("target"), str) else ""
        items.append({
            "event": shown,
            "index": index,
            "plugin": plugin,
            "reachable": _mark(target, exists, compatibility=compat, load_error=load_error),
        })
    if isinstance(raw_errors, list):
        for index, _text in enumerate(raw_errors):
            if index in matched:
                continue
            ref = error_refs[index] if index < len(error_refs) else None
            event = ref[0] if ref else "加载错误"
            items.append({
                "id": f"grok_cli:load-error:{event}:{index}",
                "event": event,
                "index": index,
                "plugin": "",
                "reachable": "missing",
            })
    error = None
    if isinstance(raw_errors, list) and raw_errors:
        error = _error(
            "cursor_format_not_run",
            "Grok 再扫到 Cursor 格式的 hooks，报告不会运行。这只属于 Grok CLI，不并入 Cursor。",
            len(raw_errors),
        )
    return items, error


def _cached_grok(inspect: Inspect | None, now: float | None) -> tuple[dict[str, Any] | None, str | None]:
    if inspect is not None:
        try:
            return inspect()
        except Exception:
            return None, "failed"
    moment = now if now is not None else time.monotonic()
    cached_at = float(_grok_cache["at"] or 0)
    if _grok_cache["payload"] is not None or _grok_cache["error"] is not None:
        if moment - cached_at < GROK_CACHE_S:
            return _grok_cache["payload"], _grok_cache["error"]
    payload, failure = _run_grok_inspect()
    _grok_cache["at"] = moment
    _grok_cache["payload"] = payload
    _grok_cache["error"] = failure
    return payload, failure


def _run_grok_inspect() -> tuple[dict[str, Any] | None, str | None]:
    try:
        proc = subprocess.run(
            ["grok", "inspect", "--json"],
            capture_output=True,
            text=True,
            timeout=GROK_TIMEOUT_S,
            check=False,
        )
    except FileNotFoundError:
        return None, "missing"
    except subprocess.TimeoutExpired:
        return None, "timeout"
    except OSError:
        return None, "failed"
    if proc.returncode != 0:
        return None, "failed"
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None, "invalid"
    if not isinstance(data, dict):
        return None, "invalid"
    return data, None


def _read_hermes_plugins(home: Path) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    root = home / ".hermes" / "plugins"
    if root.is_symlink():
        return [], _error("unreadable", "这个来源没读到")
    if not root.exists():
        return [], None
    try:
        entries = sorted(root.iterdir()) if root.is_dir() else []
    except OSError:
        return [], _error("unreadable", "这个来源没读到")
    items = []
    failed = False
    for plugin_dir in entries:
        if not plugin_dir.is_dir() or plugin_dir.is_symlink() or plugin_dir.name.startswith("."):
            continue
        text, reason = _read_file(plugin_dir / "plugin.yaml")
        if reason == "absent":
            continue
        if reason != "ok" or text is None:
            failed = True
            continue
        for index, event in enumerate(_yaml_events(text)):
            items.append({"event": event, "index": index, "plugin": plugin_dir.name, "reachable": "unknown"})
    if failed and not items:
        return [], _error("unreadable", "这个来源没读到")
    if failed:
        return items, _error("partial", "有的 Hermes 插件没读到，已读到的事件仍列出")
    return items, None


def _read_hermes_shell(home: Path) -> tuple[str, int]:
    text, reason = _read_file(home / ".hermes" / "config.yaml")
    if reason == "absent":
        return "collected", 0
    if reason != "ok" or text is None:
        return "unavailable", 0
    return "collected", _shell_hook_count(text)


def _yaml_events(text: str) -> list[str]:
    events: list[str] = []
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        if stripped.startswith("hooks:") or stripped.startswith("provides_hooks:"):
            rest = stripped.split(":", 1)[1].strip()
            if rest == "[]":
                index += 1
                continue
            if _EVENT.match(rest):
                events.append(rest)
                index += 1
                continue
            index += 1
            while index < len(lines):
                line = lines[index]
                if line.strip() and not line.startswith((" ", "\t")):
                    break
                item = line.strip()
                if item.startswith("- "):
                    name = item[2:].strip().strip("'\"")
                    if ":" in name:
                        key, _, value = name.partition(":")
                        name = value.strip().strip("'\"") if key.strip() in {"event", "name"} else ""
                    if _EVENT.match(name):
                        events.append(name)
                index += 1
            continue
        index += 1
    return events


def _shell_hook_count(text: str) -> int:
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith("hooks:")), None)
    if start is None:
        return 0
    rest = lines[start].split(":", 1)[1].strip()
    if rest in {"[]", "{}", "null", "~"}:
        return 0
    count = 0
    for line in lines[start + 1:]:
        if line and not line.startswith((" ", "\t")):
            break
        if line.startswith(("  - ", "    - ")):
            count += 1
    return count


