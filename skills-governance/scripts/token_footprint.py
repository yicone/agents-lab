#!/usr/bin/env python3
"""Read-only static token footprint report for skills.

Estimates the context cost of each skill's SKILL.md and the text documents
it references. Uses ceil(utf-8 bytes / 4), named bytes_div_4. Does not call
a model tokenizer and does not modify skills, inventory, or lock files.

A skill warns at token_over_budget when its eager tokens (SKILL.md, the
bytes_div_4 estimate) exceed 5,000. Referenced documents are
quantified for visibility but never gate the skill. Over-budget skills get
a downgrade suggestion: move supporting text into references/ for
on-demand loading.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import validate_skills_inventory as inventory  # noqa: E402


REPORT_NAME = "skills-token-footprint/v2"
ESTIMATOR = "bytes_div_4"
EAGER_TOKENS_WARN = 5000
TEXT_SUFFIXES = {".md", ".txt", ".yml", ".yaml", ".json"}
SKIP_DIRS = {
    "assets",
    "evals",
    "examples",
    "fixtures",
    "node_modules",
    "references",
    "scripts",
    "tests",
    "__pycache__",
}
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
CODE_RE = re.compile(r"`([^`\n]+)`")
SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)
DEFAULT_MACHINE_ROOTS = (
    Path.home() / ".agents" / "skills",
    Path.home() / ".claude" / "skills",
    Path.home() / ".codex" / "skills",
    Path.home() / ".gemini" / "config" / "skills",
    Path.home() / ".gemini" / "antigravity" / "global_skills",
    Path.home() / ".gemini" / "antigravity-cli" / "skills",
)
REFERENCES_MOVE = (
    "Move supporting material out of SKILL.md into a references/ subdirectory "
    "and load those files only when a task needs them."
)
REFERENCES_KEEP = (
    "Keep supporting documents in references/ and load them on demand. "
    "Do not inline them into SKILL.md."
)


def static_tokens(size: int) -> int:
    if size <= 0:
        return 0
    return (size + 3) // 4


def is_concrete(value: object) -> bool:
    return isinstance(value, str) and "<" not in value and ">" not in value and (
        inventory.is_relative_repo_path(value) or inventory.is_external_path(value)
    )


def clean_relative(raw: str) -> Path | None:
    text = raw.strip().strip("<>")
    if not text or "\n" in text or len(text) > 240:
        return None
    text = text.split("#", 1)[0].split("?", 1)[0].strip()
    if not text or SCHEME_RE.match(text) or text.startswith(("/", "~")):
        return None
    path = Path(text)
    if path.is_absolute() or ".." in path.parts:
        return None
    return path


def reference_strings(text: str) -> list[str]:
    found = LINK_RE.findall(text)
    found.extend(CODE_RE.findall(text))
    return found


def read_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes() if path.is_file() else b""
    except OSError:
        return b""


def referenced_files(skill_dir: Path, skill_text: str) -> list[tuple[Path, bool]]:
    root = skill_dir.resolve()
    eager = (root / "SKILL.md").resolve()
    found: dict[Path, bool] = {}

    def add(path: Path, linked: bool) -> None:
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            return
        try:
            resolved = path.resolve()
            resolved.relative_to(root)
        except (OSError, ValueError):
            return
        if resolved == eager or resolved in found:
            return
        found[resolved] = linked

    for raw in reference_strings(skill_text):
        relative = clean_relative(raw)
        if relative is not None:
            add(root / relative, True)
    references = root / "references"
    if references.is_dir():
        for path in sorted(references.rglob("*")):
            if path.is_file():
                add(path, False)
    return list(found.items())


def file_row(skill_dir: Path, path: Path, role: str, linked: bool | None = None) -> dict:
    payload = read_bytes(path)
    relative = path.resolve().relative_to(skill_dir.resolve()).as_posix()
    row = {
        "path": relative,
        "role": role,
        "bytes": len(payload),
        "static_tokens": static_tokens(len(payload)),
    }
    if linked is not None:
        row["linked"] = linked
    return row


def suggestions_for(
    warnings: list[str],
    eager_tokens: int,
    referenced_tokens: int,
) -> list[dict]:
    if not warnings:
        return []
    eager_over = eager_tokens > EAGER_TOKENS_WARN or referenced_tokens == 0
    return [
        {
            "id": "references_subdirectory",
            "summary": REFERENCES_MOVE if eager_over else REFERENCES_KEEP,
        }
    ]


def measure_skill(
    skill_dir: Path,
    name: str,
    scope: str,
    origin: str,
) -> dict:
    skill_md = skill_dir / "SKILL.md"
    eager = read_bytes(skill_md)
    text = eager.decode("utf-8", errors="replace")
    references = [
        file_row(skill_dir, path, "referenced", linked)
        for path, linked in referenced_files(skill_dir, text)
    ]
    eager_bytes = len(eager)
    eager_tokens = static_tokens(eager_bytes)
    referenced_bytes = sum(item["bytes"] for item in references)
    referenced_tokens = sum(item["static_tokens"] for item in references)
    warnings: list[str] = []
    if eager_tokens > EAGER_TOKENS_WARN:
        warnings.append("token_over_budget")
    return {
        "name": name,
        "scope": scope,
        "origin": origin,
        "path": str(skill_dir.resolve()),
        "status": "measured",
        "reason": None,
        "eager_bytes": eager_bytes,
        "eager_tokens": eager_tokens,
        "referenced_bytes": referenced_bytes,
        "referenced_tokens": referenced_tokens,
        "package_tokens": eager_tokens + referenced_tokens,
        "package_bytes": eager_bytes + referenced_bytes,
        "files": [file_row(skill_dir, skill_md, "eager"), *references],
        "warnings": warnings,
        "suggestions": suggestions_for(warnings, eager_tokens, referenced_tokens),
    }


def unmeasured(name: str, scope: str, reason: str) -> dict:
    return {
        "name": name,
        "scope": scope,
        "origin": "inventory",
        "path": None,
        "status": "unmeasured",
        "reason": reason,
        "eager_bytes": 0,
        "eager_tokens": 0,
        "referenced_bytes": 0,
        "referenced_tokens": 0,
        "package_tokens": 0,
        "package_bytes": 0,
        "files": [],
        "warnings": [],
        "suggestions": [],
    }


def candidate_dirs(skill: dict, repo_root: Path) -> list[Path]:
    authority = skill.get("authority") if isinstance(skill.get("authority"), dict) else {}
    values: list[str] = []
    canonical = authority.get("canonical_path")
    if is_concrete(canonical):
        values.append(canonical)
    for value in skill.get("visible_paths") or []:
        if is_concrete(value):
            values.append(value)
    directories: list[Path] = []
    seen: set[Path] = set()
    for value in values:
        located = inventory.locate_path(value, repo_root)
        if located.name == "SKILL.md":
            located = located.parent
        try:
            key = located.resolve()
        except OSError:
            key = located
        if key in seen:
            continue
        seen.add(key)
        directories.append(located)
    return directories


def skill_dirs_under(root: Path, depth: int = 8) -> list[Path]:
    found: list[Path] = []
    seen: set[Path] = set()

    def add(directory: Path) -> None:
        markdown = directory / "SKILL.md"
        if not markdown.is_file():
            return
        try:
            resolved = markdown.resolve()
        except OSError:
            return
        if resolved in seen:
            return
        seen.add(resolved)
        found.append(directory)

    def walk(directory: Path, remaining: int) -> None:
        if not directory.exists():
            return
        add(directory)
        if remaining <= 0:
            return
        try:
            children = sorted(directory.iterdir(), key=lambda item: item.name)
        except OSError:
            return
        for child in children:
            if child.name.startswith(".") or child.name in SKIP_DIRS:
                continue
            if child.is_dir():
                walk(child, remaining - 1)

    if root.exists():
        walk(root, depth)
    return found


def front_name(skill_dir: Path) -> str:
    text = read_bytes(skill_dir / "SKILL.md").decode("utf-8", errors="replace")
    return inventory.skill_name_from_markdown(text) or skill_dir.name


def inventory_bindings(document: dict, repo_root: Path) -> tuple[dict[Path, tuple[str, str]], list[tuple[Path, str]], list[dict]]:
    exact: dict[Path, tuple[str, str]] = {}
    roots: list[tuple[Path, str]] = []
    missing: list[dict] = []
    skills = document.get("skills") if isinstance(document.get("skills"), list) else []
    for skill in skills:
        if not isinstance(skill, dict):
            continue
        name = skill.get("name") if isinstance(skill.get("name"), str) else ""
        scope = skill.get("scope") if isinstance(skill.get("scope"), str) else ""
        if not name:
            continue
        directories = candidate_dirs(skill, repo_root)
        found = False
        for directory in directories:
            if (directory / "SKILL.md").is_file():
                found = True
                try:
                    exact[directory.resolve()] = (name, scope)
                except OSError:
                    exact[directory] = (name, scope)
                continue
            if directory.is_dir() and skill_dirs_under(directory):
                found = True
                try:
                    roots.append((directory.resolve(), scope))
                except OSError:
                    roots.append((directory, scope))
        if not found:
            missing.append(unmeasured(name, scope, "SKILL.md not found"))
    return exact, roots, missing


def metadata_for(
    directory: Path,
    exact: dict[Path, tuple[str, str]],
    roots: list[tuple[Path, str]],
) -> tuple[str, str, str]:
    try:
        resolved = directory.resolve()
    except OSError:
        resolved = directory
    if resolved in exact:
        name, scope = exact[resolved]
        return name, scope, "inventory"
    for root, scope in roots:
        try:
            resolved.relative_to(root)
        except ValueError:
            continue
        return front_name(directory), scope, "inventory"
    return front_name(directory), "unrecorded", "scan"


def build_report(
    document: dict,
    repo_root: Path,
    scan_roots: list[Path],
    inventory_path: Path | None = None,
) -> dict:
    exact, inherited, missing = inventory_bindings(document, repo_root)
    seen: set[Path] = set()
    measured: list[dict] = []

    def add(directory: Path) -> None:
        markdown = directory / "SKILL.md"
        if not markdown.is_file():
            return
        try:
            resolved = markdown.resolve()
        except OSError:
            return
        if resolved in seen:
            return
        seen.add(resolved)
        name, scope, origin = metadata_for(directory, exact, inherited)
        measured.append(measure_skill(directory, name, scope, origin))

    for directory in exact:
        add(directory)
    for root, _scope in inherited:
        for directory in skill_dirs_under(root):
            add(directory)
    for root in scan_roots:
        for directory in skill_dirs_under(root):
            add(directory)

    rows = missing + measured
    rows.sort(
        key=lambda item: (
            0 if item["warnings"] else 1,
            0 if item["status"] == "measured" else 1,
            -item["package_tokens"],
            item["name"],
            item["path"] or "",
        )
    )
    token_hits = sum("token_over_budget" in item["warnings"] for item in rows)
    return {
        "report": REPORT_NAME,
        "read_only": True,
        "estimator": ESTIMATOR,
        "estimator_note": "package_tokens and static_tokens = ceil(utf-8 bytes / 4)",
        "thresholds": {"eager_tokens": EAGER_TOKENS_WARN},
        "inventory": str(inventory_path) if inventory_path else None,
        "gate": "fail" if token_hits else "pass",
        "summary": {
            "skills": len(rows),
            "measured": sum(item["status"] == "measured" for item in rows),
            "unmeasured": sum(item["status"] == "unmeasured" for item in rows),
            "token_over_budget": token_hits,
            "warnings": token_hits,
        },
        "skills": rows,
    }


def render_text(report: dict) -> str:
    summary = report["summary"]
    thresholds = report["thresholds"]
    lines = [
        "Token Footprint",
        f"Inventory: {report['inventory']}",
        "Read-only: yes",
        f"Estimator: {report['estimator']} ({report['estimator_note']})",
        f"Thresholds: eager_tokens>{thresholds['eager_tokens']}",
        f"Gate: {report['gate']}",
        (
            "Summary: "
            f"skills={summary['skills']} measured={summary['measured']} "
            f"unmeasured={summary['unmeasured']} "
            f"token_over_budget={summary['token_over_budget']}"
        ),
    ]
    if not report["skills"]:
        lines.append("Skills: none")
    for skill in report["skills"]:
        lines.append(f"- skill: {skill['name']}")
        lines.append(f"  scope: {skill['scope'] or 'unknown'}")
        lines.append(f"  status: {skill['status']}")
        if skill["path"]:
            lines.append(f"  path: {skill['path']}")
        if skill["reason"]:
            lines.append(f"  reason: {skill['reason']}")
        lines.append(f"  eager_bytes: {skill['eager_bytes']}")
        lines.append(f"  eager_tokens: {skill['eager_tokens']}")
        lines.append(f"  referenced_tokens: {skill['referenced_tokens']}")
        lines.append(f"  package_tokens: {skill['package_tokens']}")
        if skill["warnings"]:
            lines.append("  warnings: " + ", ".join(skill["warnings"]))
        else:
            lines.append("  warnings: none")
        for item in skill["files"]:
            if item["role"] == "referenced":
                lines.append(
                    f"  reference: {item['path']} tokens={item['static_tokens']} "
                    f"bytes={item['bytes']} linked={item['linked']}"
                )
        for item in skill["suggestions"]:
            lines.append(f"  suggestion: {item['id']} — {item['summary']}")
    return "\n".join(lines) + "\n"


def machine_roots(repo_root: Path, extra: list[Path], include_defaults: bool) -> list[Path]:
    roots: list[Path] = []
    if include_defaults:
        roots.extend(DEFAULT_MACHINE_ROOTS)
    roots.extend(extra)
    roots.extend(repo_root / relative for relative in ("skills", "skills-governance/skills", ".agents/skills", ".codex/skills"))
    unique: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        absolute = root.expanduser().absolute()
        if absolute in seen or not absolute.is_dir():
            continue
        seen.add(absolute)
        unique.append(absolute)
    return unique


def emit(payload: dict, fmt: str, output: Path | None) -> int:
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n" if fmt == "json" else render_text(payload)
    if output is None:
        sys.stdout.write(text)
    else:
        output.write_text(text, encoding="utf-8")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only skill token footprint report")
    parser.add_argument("--inventory", type=Path, default=inventory.DEFAULT_INVENTORY)
    parser.add_argument("--repo-root", type=Path, default=inventory.DEFAULT_REPO_ROOT)
    parser.add_argument("--scan-root", type=Path, action="append", default=[])
    parser.add_argument("--no-default-roots", action="store_true")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--check", action="store_true", help="exit 2 when the footprint gate fails")
    args = parser.parse_args(argv)
    inventory_path = args.inventory.expanduser()
    repo_root = args.repo_root.expanduser().resolve()
    try:
        document = json.loads(inventory_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"token footprint failed: {exc}", file=sys.stderr)
        return 1
    if not isinstance(document, dict):
        print("token footprint failed: inventory must be a JSON object", file=sys.stderr)
        return 1
    roots = machine_roots(
        repo_root,
        [path.expanduser() for path in args.scan_root],
        include_defaults=not args.no_default_roots,
    )
    report = build_report(document, repo_root, roots, inventory_path)
    code = emit(report, args.format, args.output)
    if args.check and report["gate"] == "fail":
        return 2
    return code


if __name__ == "__main__":
    sys.exit(main())
