"""Read-only load probes for Codex, Cursor, and Hermes.

Disk presence is never load evidence. A probe may set runtime_loaded false
only for an explicit opt-out. Names or paths absent from that opt-out stay
unverified. This module does not run runtime CLIs: Hermes' skill list creates
hub directories when they are missing.
"""
from __future__ import annotations

import errno
import os
import stat
import tomllib
from datetime import datetime, timezone
from pathlib import Path

# Hermes refuses to disable this skill even if it appears in skills.disabled.
HERMES_ESSENTIAL = frozenset({"hermes-agent"})
MAX_BYTES = 1_048_576
_FILE_FLAGS = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)

CODEX_PATH = Path.home() / ".codex" / "config.toml"
HERMES_PATH = Path.home() / ".hermes" / "config.yaml"


def collect_runtime_load(
    *,
    codex_path: Path = CODEX_PATH,
    hermes_path: Path = HERMES_PATH,
) -> dict[str, dict]:
    return {
        "codex": _codex_probe(codex_path),
        "cursor": _cursor_probe(),
        "hermes": _hermes_probe(hermes_path),
    }


def blocked_item_ids(items: list[dict], probe: dict | None) -> set[int]:
    """Items an explicit opt-out identifies. Unmatched paths stay unverified."""
    if not probe or probe.get("state") != "partial":
        return set()
    if probe.get("provider") == "codex":
        disabled = probe.get("disabled") or []
        return {
            id(item) for item in items
            if _item_dirs(item) & {row["dir"] for row in disabled}
        }
    if probe.get("provider") == "hermes":
        names = set(probe.get("disabled_names") or []) - HERMES_ESSENTIAL
        return {
            id(item) for item in items
            if isinstance(item.get("name"), str) and item["name"] in names and _item_dirs(item)
        }
    return set()


def parse_codex_skills_config(text: str) -> dict:
    try:
        data = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, UnicodeError):
        return {"status": "invalid"}
    if not isinstance(data, dict) or not isinstance(data.get("skills"), dict):
        return {"status": "absent_key"}
    config = data["skills"].get("config") if "config" in data["skills"] else None
    if config is None:
        return {"status": "absent_key"}
    if not isinstance(config, list):
        return {"status": "invalid"}
    disabled, enabled, skipped = [], 0, 0
    for item in config:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str) or not isinstance(item.get("enabled"), bool):
            skipped += 1
            continue
        if item["enabled"]:
            enabled += 1
            continue
        directory, name = _skill_identity(item["path"])
        if not directory or not name:
            skipped += 1
            continue
        disabled.append({"dir": directory, "name": name})
    return {"status": "ok", "disabled": disabled, "enabled_count": enabled, "skipped": skipped}


def parse_hermes_disabled(text: str) -> dict:
    """Read only the direct ``skills.disabled`` list.

    ``skills.<other>.disabled`` is a different key. A nested list must not
    become the opt-out set. A repeated top-level ``skills`` key is unreliable,
    including ``skills:``, ``skills: `` and ``skills: # comment``. The first
    key must not be parsed. Ambiguous or unreadable values stay invalid so
    callers leave the runtime unverified.
    """
    lines = text.splitlines()
    skills_lines = [i for i, line in enumerate(lines) if _top_level_skills_key(line)]
    if not skills_lines:
        return {"status": "absent_key"}
    if len(skills_lines) != 1:
        return {"status": "invalid"}
    skills_at = skills_lines[0]
    block: list[tuple[int, int, str]] = []
    for index in range(skills_at + 1, len(lines)):
        line = lines[index]
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = _indent_width(line)
        if indent is None:
            return {"status": "invalid"}
        if indent == 0:
            break
        block.append((index, indent, line.strip()))
    if not block:
        return {"status": "absent_key"}
    child_indent = min(indent for _, indent, _ in block)
    direct = [entry for entry in block if entry[1] == child_indent and _mapping_key(entry[2]) == "disabled"]
    if not direct:
        return {"status": "absent_key"}
    if len(direct) != 1:
        return {"status": "invalid"}
    index, indent, stripped = direct[0]
    rest = _strip_comment(stripped.split(":", 1)[1].strip())
    if rest == "[]":
        return {"status": "ok", "names": []}
    if rest.startswith("[") and rest.endswith("]"):
        names = [_yaml_scalar(part.strip()) for part in _split_flow(rest[1:-1])]
        if any(name is None for name in names):
            return {"status": "invalid"}
        return {"status": "ok", "names": [name for name in names if name]}
    if rest == "":
        names = []
        for line in lines[index + 1:]:
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            item_indent = _indent_width(line)
            if item_indent is None:
                return {"status": "invalid"}
            if item_indent <= indent:
                break
            item = line.strip()
            if not item.startswith("- "):
                return {"status": "invalid"}
            name = _yaml_scalar(_strip_comment(item[2:].strip()))
            if name is None:
                return {"status": "invalid"}
            if name:
                names.append(name)
        return {"status": "ok", "names": names}
    return {"status": "invalid"}


def _codex_probe(path: Path) -> dict:
    status, text, mtime = _read_text(path)
    if status != "ok":
        return _file_probe("codex", "codex_config", status, mtime)
    parsed = parse_codex_skills_config(text or "")
    if parsed["status"] != "ok":
        return _public("codex", "codex_config", "unavailable", "none", _PARSE_DETAIL["codex"], _iso(mtime))
    disabled = parsed["disabled"]
    enabled = parsed["enabled_count"]
    skipped = parsed["skipped"]
    if disabled:
        detail = (
            f"已读 skills.config。{len(disabled)} 条 enabled=false 标为未加载；"
            f"{enabled} 条 enabled=true 只表示配置启用，未标成已加载；未列出的技能保持未验证。"
        )
        if skipped:
            detail += f" {skipped} 条字段不完整，已忽略。"
        probe = _public("codex", "codex_config", "partial", "explicit_opt_out", detail, _iso(mtime))
        probe["disabled"] = disabled
        return probe
    detail = (
        f"已读 skills.config，没有 enabled=false。{enabled} 条 enabled=true 未标成已加载；"
        "未列出的技能保持未验证。"
    )
    if skipped:
        detail += f" {skipped} 条字段不完整，已忽略。"
    return _public("codex", "codex_config", "unverified", "none", detail, _iso(mtime))


def _hermes_probe(path: Path) -> dict:
    status, text, mtime = _read_text(path)
    if status != "ok":
        return _file_probe("hermes", "hermes_config", status, mtime)
    parsed = parse_hermes_disabled(text or "")
    if parsed["status"] == "absent_key":
        return _public("hermes", "hermes_config", "unverified", "none",
                       "配置里没有 skills.disabled。缺少禁用名单不等于已加载，保持未验证。", _iso(mtime))
    if parsed["status"] != "ok":
        return _public("hermes", "hermes_config", "unverified", "none", _PARSE_DETAIL["hermes"], _iso(mtime))
    names = list(dict.fromkeys(parsed["names"]))
    actionable = [name for name in names if name not in HERMES_ESSENTIAL]
    essential = len(names) - len(actionable)
    if actionable:
        detail = (
            f"已读 skills.disabled，{len(actionable)} 条显式禁用标为未加载。"
            "名单外的技能保持未验证，不根据磁盘入口推断已加载。"
        )
        if essential:
            detail += " hermes-agent 即使出现在名单里也不标成未加载，runtime 不允许禁用它。"
        probe = _public("hermes", "hermes_config", "partial", "explicit_opt_out", detail, _iso(mtime))
        probe["disabled_names"] = actionable
        return probe
    detail = "已读 skills.disabled，没有可采纳的显式禁用。空名单不等于已加载，其余技能保持未验证。"
    if essential:
        detail += " hermes-agent 在名单里，但 runtime 不允许禁用它，因此未标成未加载。"
    return _public("hermes", "hermes_config", "unverified", "none", detail, _iso(mtime))


def _cursor_probe() -> dict:
    return _public(
        "cursor", "cursor_unavailable", "unverified", "none",
        "没有逐条加载清单。不把技能目录或最近使用记录当成已加载。",
        None,
    )


_FILE_DETAIL = {
    "absent": "没有读到配置文件，加载状态未验证。",
    "denied": "配置文件无法读取，加载状态未验证。",
    "symlink": "配置文件是符号链接，未跟随读取，加载状态未验证。",
    "not_regular": "配置文件不是普通文件，未读取，加载状态未验证。",
    "too_large": "配置文件超过读取上限，加载状态未验证。",
    "invalid": "配置文件无法按文本读取，加载状态未验证。",
    "io_error": "配置文件读取失败，加载状态未验证。",
}
_PARSE_DETAIL = {
    "codex": "skills.config 无法解析，加载状态未验证。",
    "hermes": "skills.disabled 无法解析，加载状态未验证。",
}


def _file_probe(provider: str, source: str, status: str, mtime: float | None) -> dict:
    return _public(provider, source, "unavailable", "none", _FILE_DETAIL.get(status, _FILE_DETAIL["io_error"]), _iso(mtime))


def _public(provider: str, source: str, state: str, credibility: str, detail: str, collected_at: str | None) -> dict:
    return {
        "provider": provider,
        "source": source,
        "state": state,
        "credibility": credibility,
        "detail": detail,
        "collected_at": collected_at,
    }


def _item_dirs(item: dict) -> set[str]:
    found: set[str] = set()

    def add(value) -> None:
        if not isinstance(value, str) or not value.strip():
            return
        expanded = os.path.abspath(os.path.expanduser(value.strip()))
        if os.path.basename(expanded) == "SKILL.md":
            expanded = os.path.dirname(expanded)
        if expanded:
            found.add(expanded)

    for entry in item.get("entries") or []:
        if isinstance(entry, dict):
            add(entry.get("path"))
            add(entry.get("resolved"))
    for record in item.get("evidence") or []:
        if isinstance(record, dict):
            add(record.get("path"))
            add(record.get("lexical"))
            add(record.get("resolved"))
    return found


def _skill_identity(path: str) -> tuple[str, str]:
    expanded = os.path.abspath(os.path.expanduser(path.strip()))
    if os.path.basename(expanded) == "SKILL.md":
        expanded = os.path.dirname(expanded)
    name = os.path.basename(expanded)
    if not expanded or expanded in ("/", ".") or not name or name == "SKILL.md":
        return "", ""
    return expanded, name


def _read_text(path: Path) -> tuple[str, str | None, float | None]:
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return "absent", None, None
    except PermissionError:
        return "denied", None, None
    except OSError:
        return "io_error", None, None
    if stat.S_ISLNK(info.st_mode):
        return "symlink", None, None
    if not stat.S_ISREG(info.st_mode):
        return "not_regular", None, None
    if info.st_size > MAX_BYTES:
        return "too_large", None, None
    try:
        handle = os.open(path, _FILE_FLAGS)
    except PermissionError:
        return "denied", None, None
    except OSError as exc:
        return ("symlink", None, None) if exc.errno == errno.ELOOP else ("io_error", None, None)
    try:
        with os.fdopen(handle, "r", encoding="utf-8") as file:
            return "ok", file.read(), info.st_mtime
    except UnicodeError:
        return "invalid", None, None
    except OSError:
        return "io_error", None, None


def _indent_width(line: str) -> int | None:
    prefix = line[:len(line) - len(line.lstrip(" \t"))]
    if "\t" in prefix:
        return None
    return len(line) - len(line.lstrip(" "))


def _top_level_skills_key(line: str) -> bool:
    if not line or line[0] in " \t":
        return False
    head, sep, rest = line.partition(":")
    if sep != ":" or head != "skills":
        return False
    if not rest.strip():
        return True
    return rest[0] in " \t" and rest.lstrip(" \t").startswith("#")


def _mapping_key(stripped: str) -> str | None:
    if stripped.startswith("- ") or ":" not in stripped:
        return None
    key = stripped.split(":", 1)[0]
    if not key or any(char.isspace() for char in key) or key[0] in "\"'[{":
        return None
    return key


def _yaml_scalar(value: str) -> str | None:
    if not value:
        return ""
    if value[0] == '"' and value[-1] == '"' and len(value) >= 2:
        inner = value[1:-1]
        if "\\" in inner or '"' in inner:
            return None
        return inner.strip()
    if value[0] in "\"'[{" or value in ("null", "~", "|", ">") or ": " in value or value.endswith(":"):
        return None
    if any(char.isspace() for char in value) and not value.startswith('"'):
        return None
    return value


def _strip_comment(value: str) -> str:
    if value.startswith(("'", '"')):
        return value
    return value.split(" #", 1)[0].strip()


def _split_flow(value: str) -> list[str]:
    parts, buf, quote = [], [], False
    for char in value:
        if char == '"':
            quote = not quote
            buf.append(char)
        elif char == "," and not quote:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(char)
    parts.append("".join(buf))
    return parts


def _iso(mtime: float | None) -> str | None:
    if mtime is None:
        return None
    return datetime.fromtimestamp(mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
