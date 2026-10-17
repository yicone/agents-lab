#!/usr/bin/env python3
"""Declared SKILL.md capabilities checked against the SkilLock behavior surface.

The declaration lives in SKILL.md frontmatter as `capabilities.network`,
`capabilities.secrets`, and `capabilities.filesystem`. Observation is the
`behavior` map SkilLock writes into `skills.lock` (`network_urls`,
`file_reads`, `file_writes`, `shell_commands`, `allowed_tools`). This module
does not scan skill text with its own detectors.

When that map is absent, behavior is unmeasured. Unmeasured is not a pass
for a high-risk declaration that still lacks human review: those skills must
be `status: quarantined` or `customization_mode: policy_exclusion`, and
neither state can be promoted to global scope.

The inventory audit has one extra rule that the upgrade gate does not: if
behavior is unmeasured and `capabilities` is absent, the existing
`skill_guard` `network_behavior` and `secret_access` patterns on `SKILL.md`
and `scripts/` mark the skill high risk. That is not `capability_overreach`.
"""
from __future__ import annotations

import argparse
import json
import sys
from fnmatch import fnmatchcase
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import skill_layout  # noqa: E402
import validate_skills_inventory as inventory  # noqa: E402


REPORT_NAME = "skills-capability-contract/v1"
BEHAVIOR_FIELDS = (
    "shell_commands",
    "network_urls",
    "file_reads",
    "file_writes",
    "allowed_tools",
    "bundled_scripts",
)
NETWORK_SHELL = frozenset(
    {
        "aria2c",
        "curl",
        "fetch",
        "httpie",
        "nc",
        "ncat",
        "netcat",
        "scp",
        "sftp",
        "ssh",
        "wget",
        "wget2",
    }
)
WRITE_TOOLS = frozenset({"edit", "notebookedit", "write"})
READ_TOOLS = frozenset({"read"})
# Default protected paths from SkilLock policy (`.skil-lock.yaml` protected_paths).
PROTECTED_GLOBS = (
    "**/.env",
    "**/.env.*",
    "**/.netrc",
    "**/.ssh/**",
    "**/*.key",
    "**/*.pem",
    "**/*credentials*",
    "**/*secret*",
    "**/id_ed25519",
    "**/id_rsa",
    "**/secrets/**",
)
FILESYSTEM_RANK = {"none": 0, "read": 1, "write": 2}


def empty_behavior() -> dict[str, list[str]]:
    return {field: [] for field in BEHAVIOR_FIELDS}


def unquote(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        return text[1:-1]
    return text


def split_frontmatter(text: str) -> str | None:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.startswith("---\n") and normalized != "---":
        return None
    parts = normalized.split("---", 2)
    if len(parts) < 3:
        return None
    return parts[1]


def skill_name(text: str, fallback: str) -> str:
    front = split_frontmatter(text)
    if front is None:
        return fallback
    for line in front.splitlines():
        if line.startswith("name:"):
            name = unquote(line.split(":", 1)[1])
            if name:
                return name
    return fallback


def parse_inline_list(value: str) -> list[str] | None:
    if not (value.startswith("[") and value.endswith("]")):
        return None
    inner = value[1:-1].strip()
    if not inner:
        return []
    return [unquote(part) for part in inner.split(",")]


def parse_capabilities(text: str) -> dict | None:
    """Return None when `capabilities` is absent, or an error/normalized map."""
    front = split_frontmatter(text)
    if front is None:
        return None
    lines = front.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line.startswith("capabilities:"):
            start = index
            break
    if start is None:
        return None
    inline = lines[start].split(":", 1)[1].strip()
    if inline:
        return {"error": "capabilities must be a map of network, secrets, and filesystem"}
    block: list[str] = []
    for line in lines[start + 1 :]:
        if line.strip() and not line.startswith((" ", "\t")):
            break
        block.append(line)
    parsed, error = parse_map_block(block)
    if error:
        return {"error": error}
    return normalize_capabilities(parsed)


def parse_map_block(lines: list[str]) -> tuple[dict, str | None]:
    parsed: dict = {}
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip() or line.strip().startswith("#"):
            index += 1
            continue
        stripped = line.strip()
        if stripped.startswith("-") or ":" not in stripped:
            return {}, "capabilities entries must be key/value pairs"
        key, raw = stripped.split(":", 1)
        key = key.strip()
        value = raw.strip()
        if not value:
            items: list[str] = []
            index += 1
            while index < len(lines):
                item = lines[index]
                if not item.strip():
                    index += 1
                    continue
                item_text = item.strip()
                if not item_text.startswith("- "):
                    break
                items.append(unquote(item_text[2:]))
                index += 1
            parsed[key] = items
            continue
        inline = parse_inline_list(value)
        parsed[key] = inline if inline is not None else unquote(value)
        index += 1
    return parsed, None


def normalize_capabilities(parsed: dict) -> dict:
    allowed = {"network", "secrets", "filesystem"}
    unknown = sorted(set(parsed) - allowed)
    if unknown:
        return {"error": "capabilities has unknown keys: " + ", ".join(unknown)}
    network, network_error = normalize_network(parsed.get("network", "false"))
    if network_error:
        return {"error": network_error}
    secrets, secrets_error = normalize_bool(parsed.get("secrets", "false"), "capabilities.secrets")
    if secrets_error:
        return {"error": secrets_error}
    filesystem = parsed.get("filesystem", "none")
    if isinstance(filesystem, list) or filesystem not in {"none", "read", "write"}:
        return {"error": "capabilities.filesystem must be none, read, or write"}
    return {"network": network, "secrets": secrets, "filesystem": filesystem}


def normalize_bool(value: object, label: str) -> tuple[bool | None, str | None]:
    if value is True or value == "true":
        return True, None
    if value is False or value == "false":
        return False, None
    return None, f"{label} must be true or false"


def normalize_network(value: object) -> tuple[bool | list[str] | None, str | None]:
    if isinstance(value, list):
        if not all(isinstance(item, str) and item for item in value):
            return None, "capabilities.network allowlist entries must be non-empty strings"
        return value, None
    if value is True or value == "true":
        return True, None
    if value is False or value == "false":
        return False, None
    return None, "capabilities.network must be true, false, or a list of host globs"


def parse_lock_behaviors(text: str) -> dict[str, dict[str, list[str]]] | None:
    """Parse a SkilLock `skills.lock` behavior map. None when it is not a lock."""
    if "schema_version:" not in text or "\nskills:" not in f"\n{text}":
        return None
    lines = []
    for raw in text.replace("\r\n", "\n").splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        lines.append(raw.rstrip())
    skills_at = None
    for index, line in enumerate(lines):
        if line == "skills:" or line.startswith("skills:"):
            skills_at = index
            break
    if skills_at is None:
        return None
    inline = lines[skills_at].split(":", 1)[1].strip()
    if inline == "{}":
        return {}
    if inline:
        return None
    behaviors: dict[str, dict[str, list[str]]] = {}
    index = skills_at + 1
    while index < len(lines):
        line = lines[index]
        indent = len(line) - len(line.lstrip(" "))
        if indent == 0:
            break
        if indent != 2 or not line.endswith(":") or line.lstrip().startswith("-"):
            return None
        name = line.strip()[:-1]
        index += 1
        behavior = empty_behavior()
        found = False
        while index < len(lines):
            nested = lines[index]
            nested_indent = len(nested) - len(nested.lstrip(" "))
            if nested_indent <= 2:
                break
            key = nested.strip()
            if key == "behavior:" or key.startswith("behavior:"):
                found = True
                index += 1
                parsed, index, error = parse_behavior_lists(lines, index)
                if error:
                    return None
                behavior.update(parsed)
                continue
            index += 1
        if not found:
            return None
        behaviors[name] = behavior
    return behaviors


def parse_behavior_lists(
    lines: list[str], index: int
) -> tuple[dict[str, list[str]], int, str | None]:
    parsed: dict[str, list[str]] = {}
    while index < len(lines):
        line = lines[index]
        indent = len(line) - len(line.lstrip(" "))
        if indent < 6:
            break
        if indent != 6 or ":" not in line:
            return {}, index, "malformed behavior list"
        text = line.strip()
        if text.startswith("-"):
            return {}, index, "malformed behavior list"
        key, raw = text.split(":", 1)
        if key not in BEHAVIOR_FIELDS:
            index += 1
            continue
        value = raw.strip()
        if value == "[]":
            parsed[key] = []
            index += 1
            continue
        inline = parse_inline_list(value) if value.startswith("[") else None
        if inline is not None:
            parsed[key] = inline
            index += 1
            continue
        if value:
            return {}, index, "malformed behavior list"
        items, index, error = read_block_sequence(lines, index + 1, indent)
        if error:
            return {}, index, error
        parsed[key] = items
    return parsed, index, None


def read_block_sequence(
    lines: list[str], index: int, key_indent: int
) -> tuple[list[str], int, str | None]:
    """Read a block sequence under a behavior key.

    `index` is the first line after the key. Items may sit deeper than the
    key, or at the same indent (`network_urls:` then `- https://...`). A
    same-indent URL is legal YAML. Leaving it unread and storing `[]` reports
    a measurement that dropped the observation.
    """
    if index >= len(lines):
        return [], index, None
    peek = lines[index]
    peek_indent = len(peek) - len(peek.lstrip(" "))
    peek_text = peek.strip()
    if peek_indent < key_indent:
        return [], index, None
    if peek_indent == key_indent and not peek_text.startswith("- "):
        return [], index, None
    if not peek_text.startswith("- "):
        return [], index, "malformed behavior list"
    item_indent = peek_indent
    items: list[str] = []
    while index < len(lines):
        item = lines[index]
        item_indent_now = len(item) - len(item.lstrip(" "))
        item_text = item.strip()
        if item_indent_now < item_indent:
            break
        if item_indent_now == key_indent and not item_text.startswith("- "):
            break
        if item_indent_now != item_indent or not item_text.startswith("- "):
            return [], index, "malformed behavior list"
        items.append(unquote(item_text[2:]))
        index += 1
    return items, index, None


def normalize_path(value: str) -> str:
    text = value.replace("\\", "/").strip()
    while text.startswith("./"):
        text = text[2:]
    return text.strip("/")


def glob_match(pattern: str, path: str) -> bool:
    pattern_parts = [part for part in normalize_path(pattern).split("/") if part]
    path_parts = [part for part in normalize_path(path).split("/") if part]
    if not pattern_parts or not path_parts:
        return False
    return match_segments(pattern_parts, path_parts)


def match_segments(pattern: list[str], path: list[str]) -> bool:
    if not pattern:
        return not path
    head, rest = pattern[0], pattern[1:]
    if head == "**":
        return any(match_segments(rest, path[index:]) for index in range(len(path) + 1))
    if not path:
        return False
    if fnmatchcase(path[0], head) and match_segments(rest, path[1:]):
        return True
    return False


def host_of(url: str) -> str:
    text = url.strip()
    if "://" in text:
        text = text.split("://", 1)[1]
    text = text.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    if "@" in text:
        text = text.rsplit("@", 1)[1]
    return text.split(":", 1)[0].lower()


def network_allowed(url: str, allow: list[str]) -> bool:
    host = host_of(url)
    for entry in allow:
        token = entry.strip().lower()
        if not token:
            continue
        if token in {url.lower(), host}:
            return True
        if token.startswith("*."):
            suffix = token[1:]
            if host.endswith(suffix) and host != suffix[1:]:
                return True
        if fnmatchcase(host, token) or fnmatchcase(url.lower(), token):
            return True
    return False


def protected_hits(behavior: dict) -> list[str]:
    hits: list[str] = []
    for field in ("file_reads", "file_writes"):
        for path in behavior.get(field) or []:
            if any(glob_match(pattern, path) for pattern in PROTECTED_GLOBS):
                hits.append(path)
    return sorted(set(hits))


def filesystem_needed(behavior: dict) -> str:
    tools = {str(tool).lower() for tool in behavior.get("allowed_tools") or []}
    if behavior.get("file_writes") or tools & WRITE_TOOLS:
        return "write"
    if behavior.get("file_reads") or tools & READ_TOOLS:
        return "read"
    return "none"


def network_shells(behavior: dict) -> list[str]:
    found = {
        str(command).lower()
        for command in behavior.get("shell_commands") or []
        if str(command).lower() in NETWORK_SHELL
    }
    return sorted(found)


def violations(declared: dict | None, behavior: dict) -> list[str]:
    messages: list[str] = []
    urls = [str(item) for item in behavior.get("network_urls") or []]
    shells = network_shells(behavior)
    if declared is None:
        network_mode: bool | list[str] = False
        secrets_mode = False
        filesystem_mode = None
    else:
        network_mode = declared["network"]
        secrets_mode = declared["secrets"]
        filesystem_mode = declared["filesystem"]
    if network_mode is False:
        if urls:
            messages.append(
                "network: declared false but SkilLock network_urls is " + ", ".join(urls)
            )
        if shells:
            messages.append(
                "network: declared false but SkilLock shell_commands includes " + ", ".join(shells)
            )
    elif isinstance(network_mode, list):
        denied = [url for url in urls if not network_allowed(url, network_mode)]
        if denied:
            messages.append(
                "network: SkilLock network_urls outside the declared allowlist: " + ", ".join(denied)
            )
        if shells:
            messages.append(
                "network: a host allowlist does not grant SkilLock shell_commands " + ", ".join(shells)
            )
    hits = protected_hits(behavior)
    if secrets_mode is False and hits:
        messages.append(
            "secrets: declared false but SkilLock file path matches a protected path: "
            + ", ".join(hits)
        )
    needed = filesystem_needed(behavior)
    if filesystem_mode == "none" and needed != "none":
        messages.append(f"filesystem: declared none but SkilLock behavior requires {needed}")
    elif filesystem_mode == "read" and needed == "write":
        messages.append("filesystem: declared read but SkilLock behavior requires write")
    elif filesystem_mode is None and needed == "write":
        messages.append(
            "filesystem: undeclared but SkilLock file_writes or write tools require filesystem write"
        )
    return messages


def is_high_risk(declared: dict | None, behavior: dict | None) -> bool:
    if declared is not None:
        network = declared["network"]
        if network is True or (isinstance(network, list) and bool(network)):
            return True
        if declared["secrets"] is True or declared["filesystem"] == "write":
            return True
    if behavior is None:
        return False
    if behavior.get("network_urls") or network_shells(behavior) or protected_hits(behavior):
        return True
    return filesystem_needed(behavior) == "write"


def legacy_risk_signals(directory: Path) -> list[str]:
    """Reuse skill_guard's detectors when SkilLock did not measure this skill.

    Only SKILL.md and scripts/ are scanned. A hit is an audit signal, not
    capability_overreach, and only applies when capabilities is absent.
    """
    import skill_guard  # local import: skill_guard loads this module first

    chunks: list[str] = []
    skill_file = directory / "SKILL.md"
    if skill_file.is_file():
        try:
            chunks.append(skill_file.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            pass
    scripts = directory / "scripts"
    if scripts.is_dir():
        for path in sorted(scripts.rglob("*")):
            if not path.is_file():
                continue
            try:
                chunks.append(path.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
    blob = "\n".join(chunks)
    found: list[str] = []
    if skill_guard.NETWORK_RE.search(blob):
        found.append("network_behavior")
    if skill_guard.SECRET_RE.search(blob):
        found.append("secret_access")
    return found


def declaration_findings(roots: list[Path], behaviors: dict, measured: bool) -> dict:
    violations_found = []
    skills = []
    for root in roots:
        for directory in skill_layout.iter_skill_dirs(root):
            try:
                text = (directory / "SKILL.md").read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            name = skill_name(text, directory.name)
            declared = parse_capabilities(text)
            malformed = isinstance(declared, dict) and "error" in declared
            grant = None if declared is None or malformed else declared
            behavior = (behaviors.get(name) or empty_behavior()) if measured else None
            messages = [declared["error"]] if malformed else []
            if measured and not malformed:
                messages.extend(violations(grant, behavior or empty_behavior()))
            signals = (
                legacy_risk_signals(directory)
                if not measured and not malformed and grant is None
                else []
            )
            if messages:
                violations_found.append({"skill": name, "messages": messages})
            skills.append(
                {
                    "name": name,
                    "path": str(directory),
                    "declared": grant,
                    "malformed": malformed,
                    "high_risk": is_high_risk(grant, behavior if measured else None) or bool(signals),
                    "signals": signals,
                    "messages": messages,
                }
            )
    return {"measured": measured, "skills": skills, "violations": violations_found}


def reviewed(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def evaluate_record(
    name: str,
    declared: dict | None,
    malformed: bool,
    behavior: dict | None,
    measured: bool,
    status: str,
    review_time: object,
    customization_mode: str,
    signals: list[str] | None = None,
) -> dict:
    messages = ["capabilities frontmatter is malformed"] if malformed else []
    if isinstance(declared, dict) and declared.get("error"):
        messages = [declared["error"]]
        declared = None
        malformed = True
    if measured and not malformed:
        messages.extend(violations(declared, behavior or empty_behavior()))
    signal_hits = [
        item for item in (signals or []) if item in {"network_behavior", "secret_access"}
    ]
    use_signals = not measured and declared is None and not malformed and bool(signal_hits)
    high = is_high_risk(declared, behavior if measured else None) or use_signals
    is_reviewed = reviewed(review_time)
    quarantined = status == "quarantined"
    excluded = customization_mode == "policy_exclusion"
    reasons: list[str] = []
    if quarantined:
        reasons.append("status is quarantined")
    if excluded:
        reasons.append("customization_mode is policy_exclusion")
    if high and not is_reviewed:
        reasons.append("high-risk capability has no review_time")
    if messages:
        reasons.append("behavior exceeds the capability declaration")
    if use_signals and not is_reviewed:
        reasons.append(
            "unmeasured skill has no capability declaration and matches " + ", ".join(signal_hits)
        )
    isolation = "required" if high and not is_reviewed and not quarantined and not excluded else "ok"
    return {
        "name": name,
        "declared": declared,
        "malformed": malformed,
        "high_risk": high,
        "signals": signal_hits if use_signals else [],
        "status": status,
        "review_time": review_time if isinstance(review_time, str) else None,
        "customization_mode": customization_mode,
        "violations": messages,
        "isolation": isolation,
        "promotion": "blocked" if reasons else "allowed",
        "promotion_block_reasons": reasons,
    }


def skill_text(directory: Path) -> str | None:
    path = directory / "SKILL.md"
    if not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def inventory_skills(document: dict) -> list[dict]:
    skills = document.get("skills") if isinstance(document.get("skills"), list) else []
    return [skill for skill in skills if isinstance(skill, dict) and isinstance(skill.get("name"), str)]


def canonical_dir(skill: dict, repo_root: Path) -> Path | None:
    authority = skill.get("authority") if isinstance(skill.get("authority"), dict) else {}
    canonical = authority.get("canonical_path")
    if not isinstance(canonical, str) or not canonical:
        return None
    directory = repo_root / canonical
    if (directory / "SKILL.md").is_file():
        return directory
    return None


def build_report(
    document: dict,
    repo_root: Path,
    behaviors: dict,
    measured: bool,
    skill_roots: list[Path] | None = None,
) -> dict:
    rows = []
    by_name = {skill["name"]: skill for skill in inventory_skills(document)}
    if skill_roots:
        seen: set[str] = set()
        for root in skill_roots:
            for directory in skill_layout.iter_skill_dirs(root):
                text = skill_text(directory)
                if text is None:
                    continue
                name = skill_name(text, directory.name)
                if name in seen:
                    continue
                seen.add(name)
                record = by_name.get(name, {})
                rows.append(row_from_text(name, text, record, behaviors, measured, directory))
    else:
        for skill in inventory_skills(document):
            directory = canonical_dir(skill, repo_root)
            text = skill_text(directory) if directory is not None else None
            rows.append(row_from_text(skill["name"], text, skill, behaviors, measured, directory))
    rows.sort(key=lambda item: item["name"])
    gate = "fail" if any(row["isolation"] == "required" or row["violations"] for row in rows) else "pass"
    return {
        "report": REPORT_NAME,
        "read_only": True,
        "observation": "skil-lock" if measured else "unmeasured",
        "gate": gate,
        "note": (
            "Overreach compares SKILL.md capabilities to the SkilLock behavior map. "
            "Unmeasured means skil-lock did not produce skills.lock behavior. "
            "When behavior is unmeasured and capabilities is absent, existing "
            "skill_guard network_behavior and secret_access patterns on SKILL.md "
            "and scripts/ mark the skill high risk. That is not capability_overreach. "
            "High-risk skills without review_time must be quarantined or "
            "policy_exclusion, and those states cannot be promoted."
        ),
        "summary": {
            "skills": len(rows),
            "high_risk": sum(row["high_risk"] for row in rows),
            "isolation_required": sum(row["isolation"] == "required" for row in rows),
            "promotion_blocked": sum(row["promotion"] == "blocked" for row in rows),
            "violations": sum(bool(row["violations"]) for row in rows),
        },
        "skills": rows,
    }


def row_from_text(
    name: str,
    text: str | None,
    record: dict,
    behaviors: dict,
    measured: bool,
    directory: Path | None = None,
) -> dict:
    declared = parse_capabilities(text) if text is not None else None
    malformed = isinstance(declared, dict) and "error" in declared
    status = record.get("status") if isinstance(record.get("status"), str) else "active"
    mode = record.get("customization_mode") if isinstance(record.get("customization_mode"), str) else "none"
    behavior = (behaviors.get(name) or empty_behavior()) if measured else None
    signals: list[str] = []
    if directory is not None and not measured and not malformed and declared is None:
        signals = legacy_risk_signals(directory)
    return evaluate_record(
        name,
        declared,
        malformed,
        behavior,
        measured,
        status,
        record.get("review_time"),
        mode,
        signals,
    )


def render_text(report: dict) -> str:
    lines = [
        "Capability contract",
        f"Observation: {report['observation']}",
        f"Gate: {report['gate']}",
        (
            "summary: "
            f"skills={report['summary']['skills']} "
            f"high_risk={report['summary']['high_risk']} "
            f"isolation_required={report['summary']['isolation_required']} "
            f"promotion_blocked={report['summary']['promotion_blocked']} "
            f"violations={report['summary']['violations']}"
        ),
    ]
    for skill in report["skills"]:
        if skill["promotion"] == "allowed" and not skill["high_risk"] and not skill["violations"]:
            continue
        lines.append(f"- skill: {skill['name']}")
        lines.append(f"  status: {skill['status']}")
        lines.append(f"  customization_mode: {skill['customization_mode']}")
        lines.append(f"  review_time: {skill['review_time'] or 'null'}")
        lines.append(f"  high_risk: {str(skill['high_risk']).lower()}")
        lines.append(f"  isolation: {skill['isolation']}")
        lines.append(f"  promotion: {skill['promotion']}")
        if skill.get("signals"):
            lines.append("  signals: " + ", ".join(skill["signals"]))
        if skill["promotion_block_reasons"]:
            lines.append("  block_reasons: " + "; ".join(skill["promotion_block_reasons"]))
        if skill["violations"]:
            for message in skill["violations"]:
                lines.append(f"  violation: {message}")
    if report["gate"] == "pass" and report["summary"]["promotion_blocked"] == 0:
        lines.append("No high-risk, isolation, or overreach findings.")
    return "\n".join(lines) + "\n"


def measure(binary: str | None, roots: list[Path]) -> tuple[dict, bool]:
    if not binary or not roots:
        return {}, False
    import tempfile

    try:
        with tempfile.TemporaryDirectory(prefix="capability-contract-") as directory:
            view = Path(directory) / "view"
            skill_layout.project_skills(roots, view)
            return skill_layout.read_behavior(binary, view, Path(directory) / "skills.lock")
    except OSError:
        return {}, False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check skill capability declarations against SkilLock")
    parser.add_argument("--inventory", type=Path, default=inventory.DEFAULT_INVENTORY)
    parser.add_argument("--repo-root", type=Path, default=inventory.DEFAULT_REPO_ROOT)
    parser.add_argument("--skill-root", action="append", type=Path, default=[])
    parser.add_argument("--promote", help="exit 2 with --check when this skill cannot be promoted")
    parser.add_argument(
        "--skil-lock",
        help="skil-lock binary used to read behavior; omit to leave behavior unmeasured",
    )
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--check", action="store_true", help="exit 2 when isolation or overreach fails")
    args = parser.parse_args(argv)
    try:
        document = json.loads(args.inventory.expanduser().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"capability contract failed: {exc}", file=sys.stderr)
        return 1
    if not isinstance(document, dict):
        print("capability contract failed: inventory must be a JSON object", file=sys.stderr)
        return 1
    repo_root = args.repo_root.expanduser().resolve()
    roots = [path.expanduser().resolve() for path in args.skill_root]
    behaviors: dict = {}
    measured = False
    if args.skil_lock and args.skil_lock != "fallback":
        binary, preset = skill_layout.resolve_skil_lock(args.skil_lock)
        if preset and preset.get("error"):
            print(f"capability contract failed: {preset.get('reason')}", file=sys.stderr)
            return 1
        scan_roots = roots or [
            directory
            for skill in inventory_skills(document)
            if (directory := canonical_dir(skill, repo_root)) is not None
        ]
        behaviors, measured = measure(binary, scan_roots)
    report = build_report(document, repo_root, behaviors, measured, roots or None)
    text = json.dumps(report, indent=2, ensure_ascii=False) + "\n" if args.format == "json" else render_text(report)
    sys.stdout.write(text)
    if not args.check:
        return 0
    if report["gate"] == "fail":
        return 2
    if args.promote:
        match = next((row for row in report["skills"] if row["name"] == args.promote), None)
        if match is None:
            print(f"capability contract failed: skill not in report: {args.promote}", file=sys.stderr)
            return 1
        if match["promotion"] == "blocked":
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
