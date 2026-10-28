"""Read-only hooks inventory for capability tri-state (ACM-664 / ACM-691).

Declared comes from a hooks file or ``grok inspect``. Reachable is only
whether absolute paths inside a command exist. Grok also uses
``hookLoadErrors`` and ``compatibilityStatus``; anything that does not line
up is unknown. ``enabled`` and ``trusted_hash`` are ignored. Observed is
always unknown. Command text is not returned.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

SCHEMA = 1
SOURCES = (
    ("cursor", "Cursor"),
    ("codex", "Codex"),
    ("grok_cli", "Grok CLI"),
    ("hermes", "Hermes"),
)
_EVENT = re.compile(r"^[A-Za-z0-9_.:()-]{1,80}$")
_ABS = re.compile(r"(?<![\w.~])(/[^\s\"';|&<>]+)")
_GROK_ERROR = re.compile(r"^hook hooks:([A-Za-z0-9_.:()-]+)\[(\d+)\]")
_IDENT = re.compile(r"^[A-Za-z0-9_.:()-]+$")
_INSPECT_TTL_S = 30.0
_inspect_cache: dict[str, Any] = {"at": 0.0, "value": None}

_DETAIL = {
    "path_yes": "命令里的绝对路径文件存在。观察到没有稳定运行日志。",
    "path_no": "命令里的绝对路径文件不存在。观察到没有稳定运行日志。",
    "path_unknown": "命令里没有可核对的绝对路径，可达为未知。观察到没有稳定运行日志。",
    "grok_error": "Grok 报告这条不会运行，不标成可达。观察到没有稳定运行日志。",
    "compat_unknown": "兼容状态对不上，可达为未知。观察到没有稳定运行日志。",
    "plugin": "插件事件已声明。回调能否导入未验证，可达为未知。观察到没有稳定运行日志。",
}


def read_grok_inspect(timeout: float = 8.0) -> dict[str, Any] | None:
    now = time.monotonic()
    if _inspect_cache["at"] and now - float(_inspect_cache["at"]) < _INSPECT_TTL_S:
        value = _inspect_cache["value"]
        return value if isinstance(value, dict) else None
    parsed: dict[str, Any] | None = None
    try:
        proc = subprocess.run(
            ["grok", "inspect", "--json"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            body = json.loads(proc.stdout)
            if isinstance(body, dict):
                parsed = body
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError, UnicodeError):
        parsed = None
    _inspect_cache["at"] = now
    _inspect_cache["value"] = parsed
    return parsed


def absolute_paths(command: str) -> list[str]:
    found: list[str] = []
    for match in _ABS.finditer(command or ""):
        path = match.group(1).rstrip("),")
        if path.startswith("//") or len(path) < 2:
            continue
        found.append(path)
    return found


def path_reach(command: str, exists: Callable[[str], bool], *, compatibility: str | None = None, load_error: bool = False) -> str:
    """Return yes / no / unknown. ``enabled`` is not an argument on purpose."""
    if load_error:
        return "no"
    if compatibility not in (None, "", "enabled"):
        return "unknown"
    paths = absolute_paths(command)
    if not paths:
        return "unknown"
    return "yes" if all(exists(path) for path in paths) else "no"


def _event_name(value: Any, fallback: str = "未命名") -> str:
    text = str(value or "").strip()
    return text if _EVENT.fullmatch(text) else fallback


def _item(source: str, label: str, event: str, index: int, declared: str, reachable: str, detail_key: str) -> dict[str, str]:
    return {
        "id": f"{source}:{event}:{index}",
        "source": source,
        "source_label": label,
        "event": event,
        "declared": declared,
        "reachable": reachable,
        "observed": "unknown",
        "detail": _DETAIL[detail_key],
    }


def _detail_key(reachable: str, *, load_error: bool = False, compatibility: str | None = None) -> str:
    if load_error:
        return "grok_error"
    if compatibility not in (None, "", "enabled"):
        return "compat_unknown"
    return {"yes": "path_yes", "no": "path_no"}.get(reachable, "path_unknown")


def _read_text(path: Path) -> str | None:
    try:
        if not path.is_file():
            return None
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _load_json(path: Path) -> tuple[Any, str]:
    text = _read_text(path)
    if text is None:
        return None, "unknown"
    try:
        return json.loads(text), "ok"
    except json.JSONDecodeError:
        return None, "unknown"


def _source(source_id: str, label: str, state: str, detail: str, count: int | None) -> dict[str, Any]:
    return {"id": source_id, "label": label, "state": state, "detail": detail, "count": count}


def _command_items(source: str, label: str, grouped: Any, exists: Callable[[str], bool]) -> list[dict[str, str]] | None:
    if not isinstance(grouped, dict):
        return None
    items: list[dict[str, str]] = []
    for event, body in grouped.items():
        commands = _commands_of(body)
        if commands is None:
            return None
        event_name = _event_name(event)
        for index, command in enumerate(commands):
            reachable = path_reach(command, exists)
            items.append(_item(source, label, event_name, index, "yes", reachable, _detail_key(reachable)))
    return items


def _commands_of(body: Any) -> list[str] | None:
    if not isinstance(body, list):
        return None
    commands: list[str] = []
    for entry in body:
        if not isinstance(entry, dict):
            return None
        if "command" in entry and isinstance(entry.get("command"), str):
            commands.append(entry["command"])
            continue
        nested = entry.get("hooks")
        if isinstance(nested, list):
            for hook in nested:
                if not isinstance(hook, dict) or not isinstance(hook.get("command"), str):
                    return None
                commands.append(hook["command"])
            continue
        return None
    return commands


def _cursor(home: Path, exists: Callable[[str], bool]) -> tuple[dict[str, Any], list[dict[str, str]]]:
    payload, state = _load_json(home / ".cursor" / "hooks.json")
    if state != "ok":
        return _source("cursor", "Cursor", "unknown", "用户 hooks 文件没读到，显示未知。", None), []
    hooks = payload.get("hooks") if isinstance(payload, dict) else None
    if not isinstance(hooks, dict):
        return _source("cursor", "Cursor", "unknown", "用户 hooks 文件对不上，显示未知。", None), []
    items = _command_items("cursor", "Cursor", hooks, exists)
    if items is None:
        return _source("cursor", "Cursor", "unknown", "用户 hooks 文件对不上，显示未知。", None), []
    detail = "只读用户级 hooks 文件。项目级文件未逐仓库扫描。"
    file_state = "empty" if not items else "ok"
    return _source("cursor", "Cursor", file_state, detail, len(items)), items


def _codex(home: Path, exists: Callable[[str], bool]) -> tuple[dict[str, Any], list[dict[str, str]]]:
    payload, state = _load_json(home / ".codex" / "hooks.json")
    if state != "ok":
        return _source("codex", "Codex", "unknown", "用户 hooks 文件没读到，显示未知。", None), []
    hooks = payload.get("hooks") if isinstance(payload, dict) else None
    if not isinstance(hooks, dict):
        return _source("codex", "Codex", "unknown", "用户 hooks 文件对不上，显示未知。", None), []
    items = _command_items("codex", "Codex", hooks, exists)
    if items is None:
        return _source("codex", "Codex", "unknown", "用户 hooks 文件对不上，显示未知。", None), []
    detail = "只读用户 hooks。enabled 和 trusted_hash 不参与可达或观察到。插件 hooks 未采集。"
    file_state = "empty" if not items else "ok"
    return _source("codex", "Codex", file_state, detail, len(items)), items


def _error_ref(text: str) -> tuple[str, int, str] | None:
    """Event, index, and path from a grok load-error line. The path is for matching only."""
    match = _GROK_ERROR.match(text.strip())
    if not match:
        return None
    rest = text[match.end():]
    path = ""
    marker = " in "
    if marker in rest:
        path = rest.split(marker, 1)[1].split(":", 1)[0].strip()
    return match.group(1), int(match.group(2)), path


def _grok(inspect: Callable[[], dict[str, Any] | None], exists: Callable[[str], bool]) -> tuple[dict[str, Any], list[dict[str, str]]]:
    try:
        payload = inspect()
    except Exception:
        payload = None
    if not isinstance(payload, dict):
        return _source("grok_cli", "Grok CLI", "unknown", "grok inspect 没有读到，显示未知。", None), []
    items: list[dict[str, str]] = []
    hooks = payload.get("hooks")
    errors = payload.get("hookLoadErrors")
    error_refs = [_error_ref(str(error)) for error in errors] if isinstance(errors, list) else []
    matched: set[int] = set()
    if isinstance(hooks, list):
        local_index: dict[tuple[str, str], int] = {}
        for index, hook in enumerate(hooks):
            if not isinstance(hook, dict):
                continue
            source = hook.get("source") if isinstance(hook.get("source"), dict) else {}
            source_path = source.get("path") if isinstance(source.get("path"), str) else ""
            event = _event_name(hook.get("event"))
            key = (source_path, event)
            local = local_index.get(key, 0)
            local_index[key] = local + 1
            load_error = False
            if source_path.startswith("/") and source_path != "/":
                for err_index, ref in enumerate(error_refs):
                    if ref and ref[0] == event and ref[1] == local and ref[2] == source_path:
                        load_error = True
                        matched.add(err_index)
            compatibility = hook.get("compatibilityStatus")
            compat = compatibility if isinstance(compatibility, str) else None
            target = hook.get("target") if isinstance(hook.get("target"), str) else ""
            reachable = path_reach(target, exists, compatibility=compat, load_error=load_error)
            items.append(_item(
                "grok_cli", "Grok CLI", event, index,
                "yes", reachable, _detail_key(reachable, load_error=load_error, compatibility=compat),
            ))
    if isinstance(errors, list):
        for index, error in enumerate(errors):
            if index in matched:
                continue
            ref = error_refs[index] if index < len(error_refs) else None
            event = _event_name(ref[0] if ref else "", "加载错误")
            row = _item("grok_cli", "Grok CLI", event, index, "yes", "no", "grok_error")
            row["id"] = f"grok_cli:load-error:{event}:{index}"
            items.append(row)
    if not isinstance(hooks, list) and not isinstance(errors, list):
        return _source("grok_cli", "Grok CLI", "unknown", "grok inspect 对不上，显示未知。", None), []
    detail = "以 grok inspect 为准，不和 Cursor 来源合并。加载错误不标成可达。"
    file_state = "empty" if not items else "ok"
    return _source("grok_cli", "Grok CLI", file_state, detail, len(items)), items


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _top_block(text: str, key: str) -> list[str] | None:
    lines = text.splitlines()
    start = None
    prefix = f"{key}:"
    for index, line in enumerate(lines):
        if line.startswith(prefix):
            start = index
            break
    if start is None:
        return None
    block = []
    for line in lines[start + 1:]:
        if line.strip() == "":
            continue
        if _indent(line) == 0:
            break
        block.append(line)
    return block


def _enabled_names(text: str) -> list[str] | None:
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line.startswith("plugins:"):
            start = index
            break
    if start is None:
        return None
    enabled_at = None
    enabled_indent = None
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if line.strip() == "":
            continue
        if _indent(line) == 0:
            break
        if line.strip().startswith("enabled:"):
            enabled_at = index
            enabled_indent = _indent(line)
            break
    if enabled_at is None or enabled_indent is None:
        return None
    names: list[str] = []
    for line in lines[enabled_at + 1:]:
        if line.strip() == "":
            continue
        if _indent(line) <= enabled_indent:
            break
        stripped = line.strip()
        if not stripped.startswith("- "):
            return None
        name = stripped[2:].strip().strip("'\"")
        if not _IDENT.fullmatch(name):
            return None
        names.append(name)
    return names


def _event_list(text: str, key: str) -> list[str] | None:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith(f"{key}:"):
            continue
        inline = stripped.split(":", 1)[1].strip()
        if inline == "[]":
            return []
        if inline:
            return None
        base = _indent(line)
        names: list[str] = []
        for follower in lines[index + 1:]:
            if follower.strip() == "":
                continue
            if _indent(follower) <= base:
                break
            item = follower.strip()
            if not item.startswith("- "):
                return None
            name = item[2:].strip().strip("'\"")
            if not _IDENT.fullmatch(name):
                return None
            names.append(name)
        return names
    return None


def _hermes(home: Path, exists: Callable[[str], bool]) -> tuple[dict[str, Any], list[dict[str, str]]]:
    config_path = home / ".hermes" / "config.yaml"
    text = _read_text(config_path)
    if text is None:
        return _source("hermes", "Hermes", "unknown", "配置没读到，显示未知。", None), []
    items: list[dict[str, str]] = []
    notes = ["shell 空列表不是异常。"]
    block = _top_block(text, "hooks")
    if block is None:
        shell_state = "empty"
    else:
        shell_state = "ok"
        raw = "hooks:\n" + "\n".join(block) + "\n"
        commands = _yaml_commands(raw)
        if commands is None:
            return _source("hermes", "Hermes", "unknown", "shell hooks 对不上，显示未知。", None), []
        for index, command in enumerate(commands):
            reachable = path_reach(command, exists)
            row = _item("hermes", "Hermes", "shell", index, "yes", reachable, _detail_key(reachable))
            row["id"] = f"hermes:shell:{index}"
            items.append(row)
        if not commands:
            shell_state = "empty"
    enabled = _enabled_names(text)
    if enabled is None:
        notes.append("插件启用名单没读到，插件事件为未知。")
        plugin_state = "unknown"
    else:
        plugin_state = "ok"
        for name in enabled:
            plugin = home / ".hermes" / "plugins" / name / "plugin.yaml"
            body = _read_text(plugin)
            if body is None:
                plugin_state = "unknown"
                notes.append("有启用插件的清单没读到，这些事件为未知。")
                continue
            events = _event_list(body, "hooks")
            if events is None:
                events = _event_list(body, "provides_hooks")
            if events is None:
                plugin_state = "unknown"
                notes.append("有启用插件的事件清单对不上，显示未知。")
                continue
            for index, event in enumerate(events):
                shown = _event_name(f"{name}.{event}")
                row = _item("hermes", "Hermes", shown, index, "yes", "unknown", "plugin")
                row["id"] = f"hermes:{name}:{shown}:{index}"
                items.append(row)
    if shell_state == "unknown" or plugin_state == "unknown":
        state = "unknown"
    elif not items:
        state = "empty"
    else:
        state = "ok"
    return _source("hermes", "Hermes", state, " ".join(notes), len(items) if state != "unknown" else None), items


def _yaml_commands(text: str) -> list[str] | None:
    """Read ``command:`` scalars from a small hooks list. Return None if the shape is unfamiliar."""
    commands: list[str] = []
    in_hooks = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("hooks:"):
            in_hooks = True
            continue
        if not in_hooks or not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("command:"):
            value = stripped.split(":", 1)[1].strip().strip("'\"")
            if not value:
                return None
            commands.append(value)
            continue
        if stripped.startswith("- ") and ":" not in stripped:
            return None
    return commands


def build_hooks(
    home: Path,
    *,
    generated_at: str | None = None,
    inspect: Callable[[], dict[str, Any] | None] | None = None,
    exists: Callable[[str], bool] | None = None,
) -> dict[str, Any]:
    # exists, not isfile: a redirect such as /dev/null is not a regular file, but the path is there.
    file_exists = exists or os.path.exists
    inspect_fn = inspect or read_grok_inspect
    cursor_source, cursor_items = _cursor(home, file_exists)
    codex_source, codex_items = _codex(home, file_exists)
    grok_source, grok_items = _grok(inspect_fn, file_exists)
    hermes_source, hermes_items = _hermes(home, file_exists)
    items = [*cursor_items, *codex_items, *grok_items, *hermes_items]
    for item in items:
        if item["observed"] != "unknown":
            item["observed"] = "unknown"
    return {
        "ok": True,
        "hooks_schema": SCHEMA,
        "read_only": True,
        "generated_at": generated_at,
        "items": items,
        "sources": [cursor_source, codex_source, grok_source, hermes_source],
        "notes": [
            "项目级 hooks 未逐仓库扫描，不因此告警。",
            "缺数据显示未知，不是异常。",
            "hooks 的观察到全部为未知。",
        ],
    }
