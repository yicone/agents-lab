#!/usr/bin/env python3
"""Validate the structural contract of a branch/worktree governance report.

This deliberately validates shape, not whether the report's evidence or
recommendations are correct.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Sequence


REQUIRED_SECTIONS = (
    "Audit Scope",
    "Explicit Project Constraints",
    "Task Execution Constraints",
    "Visibility Limits",
    "Source Inventory",
    "Observed Facts And Rules",
    "Observed Profile",
    "Difference Findings",
    "Unresolved Questions",
    "Tooling Opportunity",
    "Smallest Safe Next Step",
)

SOURCE_COLUMNS = ("source", "class", "scope", "normativity", "freshness", "visibility")

CORE_FIELDS = {
    "primary_branch": None,
    "primary_branch_role": {"production", "release-ready", "integration", "local-default", "upstream-mirror", "unknown"},
    "direct_primary_changes": {"prohibited", "conditional", "allowed", "unknown"},
    "worktree_policy": {"required", "conditional", "optional", "not-used", "unknown"},
    "worktree_adoption": {"active", "historical", "none", "unknown"},
    "worktree_location": {"repository-local", "sibling", "external", "unspecified"},
    "branch_roles": None,
    "branch_patterns": None,
    "upstream_mode": {"none", "mirror", "periodic-sync", "selective-adoption", "unknown"},
    "release_freeze": {"none", "on-demand", "persistent", "unknown"},
    "environment_coupling": {"none", "local-runtime", "docker", "test-server", "preview", "production", "mixed", "unknown"},
}

FORK_FIELDS = {
    "upstream_base_sync": {"mirror", "periodic-sync", "manual", "unknown"},
    "local_patch_flow": {"none", "upstream-contribution", "persistent-local", "mixed", "unknown"},
}
RUNTIME_FIELDS = {"runtime_binding": {"upstream-install", "fork-primary", "patch-branch", "worktree", "unknown"}}

DEPLOYMENT_FIELDS = {
    "deployment_topology": {"single-source", "component-specific", "external", "unknown"},
    "deployment_bindings": None,
    "deployment_triggers": None,
    "preview_behavior": {
        "none",
        "branch-preview",
        "pull-request-preview",
        "environment-preview",
        "mixed",
        "unknown",
    },
}
DEPLOYMENT_BINDING_KINDS = {"branch", "branch-pattern", "tag-pattern"}
DEPLOYMENT_TRIGGERS = {"merge", "push", "tag", "manual", "external", "unknown"}

PROFILE_FIELDS = set(CORE_FIELDS) | set(FORK_FIELDS) | set(RUNTIME_FIELDS) | set(DEPLOYMENT_FIELDS)
SEMANTIC_BRANCH_ROLES = {
    "feature",
    "fix",
    "hotfix",
    "upstream-review",
    "upstream-contribution",
    "upstream-adopt",
    "release",
    "experiment",
}

FINDING_TYPES = {
    "direct_conflict",
    "project_fit_conflict",
    "omission",
    "duplication",
    "scope_misplacement",
    "stale_guidance",
    "state_policy_confusion",
    "unverifiable_claim",
    "enforcement_gap",
    "naming_coupling",
    "git_object_confusion",
}
FINDING_ACTIONS = {
    "keep",
    "rewrite",
    "move_to_repo_entry",
    "move_to_project_doc",
    "move_to_project_skill",
    "narrow_global_default",
    "adapter_only",
    "enforce_mechanically",
    "retire",
    "clarify",
}
CONFIDENCE_VALUES = {"high", "medium", "low"}


@dataclass(frozen=True)
class Diagnostic:
    code: str
    message: str
    line: Optional[int] = None


@dataclass(frozen=True)
class Heading:
    level: int
    title: str
    line: int


@dataclass(frozen=True)
class FieldRecord:
    key: str
    value: str
    line: int
    confidence: Optional[str]
    confidence_line: Optional[int]


def _diagnostic(code: str, message: str, line: Optional[int] = None) -> Diagnostic:
    return Diagnostic(code, message, line)


def _headings(lines: Sequence[str]) -> list[Heading]:
    result: list[Heading] = []
    fenced = False
    for number, raw in enumerate(lines, start=1):
        stripped = raw.strip()
        if stripped.startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            continue
        match = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", raw.rstrip("\n"))
        if match:
            result.append(Heading(len(match.group(1)), match.group(2).strip(), number))
    return result


def _section_bounds(headings: Sequence[Heading], lines: Sequence[str], title: str) -> tuple[Optional[tuple[int, int, int]], list[Diagnostic]]:
    matches = [heading for heading in headings if heading.title == title]
    diagnostics: list[Diagnostic] = []
    if not matches:
        diagnostics.append(_diagnostic("section.missing", f"required heading '{title}' is missing"))
        return None, diagnostics
    if len(matches) > 1:
        diagnostics.append(_diagnostic("section.duplicate", f"required heading '{title}' must occur exactly once", matches[1].line))
    heading = matches[0]
    end = len(lines) + 1
    for candidate in headings:
        if candidate.line > heading.line and candidate.level <= heading.level:
            end = candidate.line
            break
    return (heading.line + 1, end, heading.level), diagnostics


def _split_cells(line: str) -> list[str]:
    value = line.strip()
    if value.startswith("|"):
        value = value[1:]
    if value.endswith("|"):
        value = value[:-1]
    return [cell.strip() for cell in value.split("|")]


def _is_separator(line: str) -> bool:
    cells = _split_cells(line)
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in cells)


def _has_unescaped_glob(value: str) -> bool:
    stripped = value.strip()
    if len(stripped) >= 2 and stripped[0] == "`" and stripped[-1] == "`":
        command = stripped[1:-1].lstrip()
        if re.match(r"(?:git|gh|rg|find|python3?)\b", command):
            return False
    escaped = False
    for character in stripped:
        if escaped:
            escaped = False
            continue
        if character == "\\":
            escaped = True
            continue
        if character in "*?[]":
            return True
    return False


def _validate_source_inventory(lines: Sequence[str], bounds: tuple[int, int, int]) -> list[Diagnostic]:
    start, end, _ = bounds
    table_start: Optional[int] = None
    separator: Optional[int] = None
    for number in range(start, end):
        line = lines[number - 1]
        if "|" not in line:
            continue
        if table_start is None:
            table_start = number
            continue
        if _is_separator(line):
            separator = number
            break
    if table_start is None or separator is None:
        return [_diagnostic("source.table", "Source Inventory must contain a Markdown table", start)]

    headers = [cell.strip("`").lower() for cell in _split_cells(lines[table_start - 1])]
    diagnostics: list[Diagnostic] = []
    if headers != list(SOURCE_COLUMNS):
        diagnostics.append(_diagnostic("source.columns", "Source Inventory table must use the six required columns in order", table_start))

    data_rows = 0
    for number in range(separator + 1, end):
        line = lines[number - 1]
        if "|" not in line:
            if data_rows:
                break
            continue
        cells = _split_cells(line)
        if not any(cells):
            continue
        data_rows += 1
        if len(cells) != len(SOURCE_COLUMNS):
            diagnostics.append(_diagnostic("source.columns", "each Source Inventory row must have six cells", number))
            continue
        source = cells[0]
        if _has_compound_inline_sources(source):
            diagnostics.append(_diagnostic("source.compound", "Source Inventory source cells must identify one exact source", number))
        if _has_unescaped_glob(source):
            diagnostics.append(_diagnostic("source.wildcard", "Source Inventory source cells must identify one exact source, not a wildcard aggregate", number))
    if data_rows == 0:
        diagnostics.append(_diagnostic("source.empty", "Source Inventory must contain at least one source record", separator + 1))
    return diagnostics


def _has_compound_inline_sources(value: str) -> bool:
    spans = re.findall(r"`([^`]+)`", value)
    return len([s for s in spans if "/" in s or s.endswith((".md", ".toml", ".yaml", ".yml"))]) > 1


def _parse_field_line(line: str) -> Optional[tuple[str, str]]:
    match = re.match(r"^\s*(?:[-*]\s+)?([A-Za-z][A-Za-z0-9_ ]*):\s*(.*?)\s*$", line)
    if not match:
        return None
    return match.group(1).strip().lower().replace(" ", "_"), match.group(2).strip()


def _parse_profile(lines: Sequence[str], bounds: tuple[int, int, int]) -> tuple[list[FieldRecord], list[Diagnostic]]:
    start, end, _ = bounds
    records: list[FieldRecord] = []
    diagnostics: list[Diagnostic] = []
    pending: Optional[tuple[str, str, int]] = None
    for number in range(start, end):
        parsed = _parse_field_line(lines[number - 1])
        if parsed is None:
            continue
        key, value = parsed
        if key == "confidence":
            if pending is None:
                diagnostics.append(_diagnostic("profile.orphan-confidence", "confidence must immediately follow a profile field", number))
                continue
            records.append(FieldRecord(pending[0], pending[1], pending[2], value, number))
            pending = None
            continue
        if pending is not None:
            records.append(FieldRecord(pending[0], pending[1], pending[2], None, None))
            diagnostics.append(_diagnostic("profile.missing-confidence", f"profile field '{pending[0]}' must have an adjacent confidence", pending[2]))
        pending = (key, value, number)
    if pending is not None:
        records.append(FieldRecord(pending[0], pending[1], pending[2], None, None))
        diagnostics.append(_diagnostic("profile.missing-confidence", f"profile field '{pending[0]}' must have an adjacent confidence", pending[2]))
    return records, diagnostics


def _list_value(value: str) -> list[str]:
    value = value.strip()
    if value.startswith("["):
        value = value[1:]
    if value.endswith("]"):
        value = value[:-1]
    if not value.strip():
        return []
    return [item.strip().strip("`") for item in value.split(",") if item.strip()]


def _parse_deployment_items(record: FieldRecord, binding: bool) -> tuple[Optional[set[str]], list[Diagnostic]]:
    code = "profile.invalid-deployment-binding" if binding else "profile.invalid-deployment-trigger"
    item_name = "binding" if binding else "trigger"
    value = record.value.strip()
    if not (value.startswith("[") and value.endswith("]")):
        return None, [
            _diagnostic("profile.invalid-list", f"profile field '{record.key}' must be a list", record.line)
        ]

    raw_items = value[1:-1].split(",")
    components: set[str] = set()
    diagnostics: list[Diagnostic] = []
    for raw_item in raw_items:
        item = raw_item.strip().strip("`")
        component, separator, item_value = item.partition("=")
        component = component.strip()
        item_value = item_value.strip()
        malformed = not separator or not component or not item_value
        if binding and not malformed:
            kind, kind_separator, literal = item_value.partition(":")
            malformed = (
                not kind_separator
                or kind.strip() not in DEPLOYMENT_BINDING_KINDS
                or not literal.strip()
            )
        if not binding and not malformed:
            malformed = item_value not in DEPLOYMENT_TRIGGERS
        if component in components:
            malformed = True
        if malformed:
            diagnostics.append(
                _diagnostic(code, f"invalid deployment {item_name} '{item}'", record.line)
            )
            continue
        components.add(component)
    return components, diagnostics


def _validate_profile(lines: Sequence[str], bounds: tuple[int, int, int]) -> list[Diagnostic]:
    records, diagnostics = _parse_profile(lines, bounds)
    seen: dict[str, FieldRecord] = {}
    for record in records:
        if record.key == "confidence":
            continue
        if record.key not in PROFILE_FIELDS:
            diagnostics.append(_diagnostic("profile.unknown-key", f"unknown Observed Profile key '{record.key}'", record.line))
        if record.key in seen:
            diagnostics.append(_diagnostic("profile.duplicate-key", f"Observed Profile key '{record.key}' must occur once", record.line))
        seen[record.key] = record
        if record.confidence not in CONFIDENCE_VALUES:
            diagnostics.append(_diagnostic("profile.invalid-confidence", f"profile field '{record.key}' must use high, medium, or low confidence", record.confidence_line or record.line))
        if record.value == "unknown" and record.confidence == "high":
            diagnostics.append(_diagnostic("profile.unknown-confidence", f"unknown profile field '{record.key}' cannot have high confidence", record.line))

    for key in CORE_FIELDS:
        if key not in seen:
            diagnostics.append(_diagnostic("profile.missing-key", f"required core profile key '{key}' is missing"))

    for group_name, group in (("fork", FORK_FIELDS), ("runtime", RUNTIME_FIELDS), ("deployment", DEPLOYMENT_FIELDS)):
        present = set(seen) & set(group)
        if len(group) > 1 and present and present != set(group):
            missing = ", ".join(sorted(set(group) - present))
            diagnostics.append(_diagnostic("profile.incomplete-extension", f"{group_name} extension is incomplete; missing {missing}"))

    for key, record in seen.items():
        allowed = CORE_FIELDS.get(key, FORK_FIELDS.get(key, RUNTIME_FIELDS.get(key, DEPLOYMENT_FIELDS.get(key))))
        if allowed is not None and record.value not in allowed:
            diagnostics.append(_diagnostic("profile.invalid-value", f"profile field '{key}' has unsupported value '{record.value}'", record.line))
        if key in {"branch_roles", "branch_patterns"}:
            values = _list_value(record.value)
            if not record.value.strip().startswith("["):
                diagnostics.append(_diagnostic("profile.invalid-list", f"profile field '{key}' must be a list", record.line))
            if key == "branch_roles":
                for role in values:
                    if role not in SEMANTIC_BRANCH_ROLES:
                        diagnostics.append(_diagnostic("profile.invalid-role", f"'{role}' is not a semantic branch role", record.line))

    deployment_components: dict[str, set[str]] = {}
    for key, binding in (("deployment_bindings", True), ("deployment_triggers", False)):
        if key not in seen:
            continue
        components, item_diagnostics = _parse_deployment_items(seen[key], binding)
        diagnostics.extend(item_diagnostics)
        if components is not None and not item_diagnostics:
            deployment_components[key] = components
    if (
        len(deployment_components) == 2
        and deployment_components["deployment_bindings"] != deployment_components["deployment_triggers"]
    ):
        diagnostics.append(
            _diagnostic(
                "profile.deployment-components",
                "deployment binding and trigger component sets must match",
                seen["deployment_triggers"].line,
            )
        )
    return diagnostics


def _finding_blocks(lines: Sequence[str], bounds: tuple[int, int, int], headings: Sequence[Heading]) -> list[tuple[int, int]]:
    start, end, level = bounds
    finding_headings = [heading for heading in headings if start <= heading.line < end and heading.level > level]
    if finding_headings:
        blocks: list[tuple[int, int]] = []
        for index, heading in enumerate(finding_headings):
            block_end = finding_headings[index + 1].line if index + 1 < len(finding_headings) else end
            blocks.append((heading.line + 1, block_end))
        return blocks
    item_lines = [number for number in range(start, end) if re.match(r"^\s*[-*]\s+type:\s*", lines[number - 1])]
    if not item_lines:
        return []
    return [(number, item_lines[index + 1] if index + 1 < len(item_lines) else end) for index, number in enumerate(item_lines)]


def _validate_findings(lines: Sequence[str], bounds: tuple[int, int, int], headings: Sequence[Heading]) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    blocks = _finding_blocks(lines, bounds, headings)
    if not blocks:
        prose = [lines[n - 1].strip().lower() for n in range(bounds[0], bounds[1]) if lines[n - 1].strip()]
        if prose in (["none"], ["none discovered"]):
            return []
        return [_diagnostic("finding.missing", "Difference Findings must contain at least one finding", bounds[0])]
    required = {"type", "difference", "evidence", "confidence", "recommended_action", "proposed_destination"}
    for block_start, block_end in blocks:
        fields: dict[str, tuple[str, int]] = {}
        for number in range(block_start, block_end):
            parsed = _parse_field_line(lines[number - 1])
            if parsed is not None:
                fields.setdefault(parsed[0], (parsed[1], number))
        for key in sorted(required - set(fields)):
            diagnostics.append(_diagnostic("finding.missing-field", f"finding is missing required field '{key.replace('_', ' ')}'", block_start))
        if "type" in fields and fields["type"][0] not in FINDING_TYPES:
            diagnostics.append(_diagnostic("finding.invalid-type", f"unsupported finding type '{fields['type'][0]}'", fields["type"][1]))
        if "confidence" in fields and fields["confidence"][0] not in CONFIDENCE_VALUES:
            diagnostics.append(_diagnostic("finding.invalid-confidence", "finding confidence must be high, medium, or low", fields["confidence"][1]))
        if "recommended_action" in fields and fields["recommended_action"][0] not in FINDING_ACTIONS:
            diagnostics.append(_diagnostic("finding.invalid-action", f"unsupported recommended action '{fields['recommended_action'][0]}'", fields["recommended_action"][1]))
        if fields.get("type", (None,))[0] == "enforcement_gap":
            for key in ("missing_effective_control", "proportionality"):
                if key not in fields:
                    diagnostics.append(_diagnostic("finding.missing-field", f"finding is missing required field '{key.replace('_', ' ')}'", block_start))
    return diagnostics


def validate_lines(lines: Sequence[str]) -> list[Diagnostic]:
    headings = _headings(lines)
    diagnostics: list[Diagnostic] = []
    bounds_by_title: dict[str, tuple[int, int, int]] = {}
    for title in REQUIRED_SECTIONS:
        bounds, section_diagnostics = _section_bounds(headings, lines, title)
        diagnostics.extend(section_diagnostics)
        if bounds is not None:
            bounds_by_title[title] = bounds
    if "Source Inventory" in bounds_by_title:
        diagnostics.extend(_validate_source_inventory(lines, bounds_by_title["Source Inventory"]))
    if "Observed Profile" in bounds_by_title:
        diagnostics.extend(_validate_profile(lines, bounds_by_title["Observed Profile"]))
    if "Difference Findings" in bounds_by_title:
        diagnostics.extend(_validate_findings(lines, bounds_by_title["Difference Findings"], headings))
    return diagnostics


def validate_report(path: Path | str) -> list[Diagnostic]:
    report_path = Path(path)
    try:
        lines = report_path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        return [_diagnostic("file.read", str(error))]
    return validate_lines(lines)


def _format_diagnostic(path: Path, diagnostic: Diagnostic) -> str:
    line = diagnostic.line or 1
    return f"{path}:{line}: {diagnostic.code}: {diagnostic.message}"


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    args = parser.parse_args(argv)
    diagnostics = validate_report(args.report)
    for diagnostic in diagnostics:
        print(_format_diagnostic(args.report, diagnostic), file=sys.stderr)
    if any(diagnostic.code == "file.read" for diagnostic in diagnostics):
        return 2
    return 1 if diagnostics else 0


if __name__ == "__main__":
    raise SystemExit(main())
