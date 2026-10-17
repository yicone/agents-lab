#!/usr/bin/env python3
"""Read-only conflict report for skills visible to the inventory.

Name collision remains the check for one name with two different SKILL.md
hashes. This command also reports responsibility overlap, behavior
divergence, scope shadowing, dependency conflicts, and trigger ambiguity.
It does not write the inventory, conflict_status, or skill files, and it
does not choose a winner.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import validate_skills_inventory as inventory  # noqa: E402


REPORT_NAME = "skills-conflict/v1"
EGO_BROWSER_ROUTING_NOTE = (
    "agent-browser 与 ego-browser 只算职责重叠，不算行为分歧。"
    "ego-browser 独有：人和 agent 共享浏览器 session，支持多个 Profile 和 Space，适合这些场景。"
    "该说明只覆盖这些场景。其余重叠按 skills-governance/docs/routing-precedence.md 停下询问。"
    "ACM-136。"
)
TYPES = (
    "name_collision",
    "responsibility_overlap",
    "behavior_divergence",
    "scope_shadowing",
    "dependency_conflict",
    "trigger_ambiguity",
)
SCOPE_RANK = {
    "repo": 1,
    "cross-repo": 1,
    "plugin-managed": 2,
    "machine": 3,
    "vendor-managed": 3,
    "app-bundled": 3,
}
IGNORE_BASENAMES = {"skill", "skills", "examples", "scripts", "references"}
PLATFORM = {
    "apple",
    "claude",
    "cloud",
    "codex",
    "cursor",
    "github",
    "google",
    "inbox",
    "journal",
    "logseq",
    "multica",
    "notion",
    "reminder",
    "slack",
    "vault",
}
STOP = {
    "about",
    "across",
    "after",
    "agent",
    "agents",
    "also",
    "and",
    "any",
    "are",
    "before",
    "deciding",
    "does",
    "especially",
    "for",
    "from",
    "how",
    "including",
    "into",
    "its",
    "must",
    "not",
    "only",
    "other",
    "per",
    "should",
    "skill",
    "skills",
    "than",
    "that",
    "the",
    "their",
    "them",
    "then",
    "this",
    "use",
    "user",
    "via",
    "what",
    "when",
    "which",
    "who",
    "with",
    "your",
}
PIN_KINDS = {"upstream_version", "runtime"}
NAME_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
TOKEN_RE = re.compile(r"[a-z][a-z0-9-]{2,}")
SPLIT_RE = re.compile(r"[\n,;:]|\band\b|\bor\b|/")
REJECT_RE = re.compile(r"\b(?:must not|do not|don't|never)\b", re.IGNORECASE)
SENT_RE = re.compile(r"[^.!?\n]+")
USE_WHEN_RE = re.compile(r"\buse when\b(.{0,280})", re.IGNORECASE)
WHEN_RE = re.compile(r"\bwhen\b(.{0,220})", re.IGNORECASE)
OWNS_RE = re.compile(r"^\s*(?:it |only )?owns\b", re.IGNORECASE)
DEP_RE = re.compile(
    r"(?:depends on|requires)\s+skill\s+`([a-z0-9]+(?:-[a-z0-9]+)*)`",
    re.IGNORECASE,
)
LIGHT_VERBS = {"get", "give", "keep", "let", "make", "put", "see"}
GENERIC_PHRASES = {
    ("browser", "task"),
    ("clarify", "question"),
    ("explicitly", "ask"),
    ("explicitly", "asks"),
    ("explicitly", "request"),
    ("feature", "work"),
    ("prefer", "over"),
    ("pull", "request"),
    ("task", "requires"),
    ("unless", "asked"),
}
DEFAULT_MACHINE_ROOTS = (
    Path.home() / ".agents" / "skills",
    Path.home() / ".claude" / "skills",
    Path.home() / ".gemini" / "config" / "skills",
    Path.home() / ".gemini" / "antigravity" / "global_skills",
    Path.home() / ".gemini" / "antigravity-cli" / "skills",
)


def stem_token(word: str) -> str:
    if len(word) > 6 and word.endswith("ing") and len(word) - 3 >= 5:
        return word[:-3]
    if len(word) > 4 and word.endswith("s") and not word.endswith(("es", "is", "ss", "us")):
        return word[:-1]
    return word


def tokens(text: str) -> list[str]:
    result = []
    for word in TOKEN_RE.findall(text.lower()):
        if word in STOP:
            continue
        stemmed = stem_token(word)
        if stemmed in STOP or len(stemmed) < 3:
            continue
        result.append(stemmed)
    return result


def keep_phrase(phrase: tuple[str, ...]) -> bool:
    if not 2 <= len(phrase) <= 3:
        return False
    if not any(len(word) >= 6 for word in phrase):
        return False
    if all(word in PLATFORM for word in phrase):
        return False
    if any(word in LIGHT_VERBS for word in phrase):
        return False
    return len(" ".join(phrase)) >= 10


def claim_phrases(text: str) -> set[tuple[str, ...]]:
    found: set[tuple[str, ...]] = set()
    for part in SPLIT_RE.split(text.lower()):
        words = tokens(part)
        for size in (2, 3):
            for index in range(0, len(words) - size + 1):
                phrase = tuple(words[index : index + size])
                if keep_phrase(phrase):
                    found.add(phrase)
    return found


def rejection_phrases(text: str) -> set[tuple[str, ...]]:
    found: set[tuple[str, ...]] = set()
    for sentence in SENT_RE.findall(text):
        if REJECT_RE.search(sentence):
            found |= claim_phrases(sentence)
    return found


def owns_text(body: str) -> str:
    chunks: list[str] = []
    capture = False
    saw_bullet = False
    for line in body.splitlines():
        stripped = line.strip()
        if OWNS_RE.search(line):
            capture = True
            saw_bullet = False
            chunks.append(stripped)
            continue
        if not capture:
            continue
        if not stripped:
            if saw_bullet:
                capture = False
            continue
        if stripped.startswith("#") or REJECT_RE.search(stripped):
            capture = False
            continue
        if stripped.startswith(("-", "*")):
            saw_bullet = True
            chunks.append(stripped)
            continue
        capture = False
    return "\n".join(chunks)


def frontmatter(text: str) -> str:
    if not text.startswith("---"):
        return ""
    parts = text.split("---", 2)
    return parts[1] if len(parts) > 2 else ""


def description_of(front: str) -> str:
    lines = front.splitlines()
    for index, line in enumerate(lines):
        if not line.startswith("description:"):
            continue
        value = line.split(":", 1)[1].strip()
        if value in {">", "|", ">-", "|-"}:
            folded = []
            for nxt in lines[index + 1 :]:
                if nxt.startswith((" ", "\t")):
                    folded.append(nxt.strip())
                else:
                    break
            return " ".join(folded).strip("\"'")
        return value.strip("\"'")
    return ""


def metadata_names(front: str, key: str) -> list[str]:
    lines = front.splitlines()
    in_metadata = False
    waiting = False
    found: list[str] = []
    for line in lines:
        if line.startswith("metadata:"):
            in_metadata = True
            waiting = False
            continue
        if in_metadata and line and not line.startswith((" ", "\t")):
            in_metadata = False
            waiting = False
        if not in_metadata:
            continue
        stripped = line.strip()
        if stripped.startswith(f"{key}:"):
            rest = stripped.split(":", 1)[1].strip()
            if rest:
                found.extend(NAME_RE.findall(rest))
                waiting = False
            else:
                waiting = True
            continue
        if waiting and stripped.startswith("-"):
            found.extend(NAME_RE.findall(stripped[1:]))
            continue
        if waiting:
            waiting = False
    return found


def trigger_tokens(description: str) -> set[str]:
    match = USE_WHEN_RE.search(description) or WHEN_RE.search(description)
    if match is None:
        return set()
    clause = match.group(1).split(".")[0]
    return {token for token in tokens(clause) if token not in PLATFORM}


def read_text(path: Path) -> str:
    try:
        if path.is_file():
            return path.read_text(encoding="utf-8")[:200_000]
    except OSError:
        return ""
    return ""


def is_bundle(skill: dict) -> bool:
    source = skill.get("source_class")
    scope = skill.get("scope")
    return (source == "runtime_builtin" and scope == "vendor-managed") or (
        source == "runtime_plugin" and scope == "plugin-managed"
    )


def is_concrete(value: object) -> bool:
    return isinstance(value, str) and "<" not in value and ">" not in value and (
        inventory.is_relative_repo_path(value) or inventory.is_external_path(value)
    )


def resolve_file(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return path


def skill_markdown(skill: dict, repo_root: Path) -> Path | None:
    authority = skill.get("authority") if isinstance(skill.get("authority"), dict) else {}
    canonical = authority.get("canonical_path")
    candidates: list[Path] = []
    if isinstance(canonical, str) and is_concrete(canonical):
        candidates.append(repo_root / canonical / "SKILL.md")
    for value in skill.get("visible_paths") or []:
        if not is_concrete(value):
            continue
        located = inventory.locate_path(value, repo_root)
        candidates.append(located if located.name == "SKILL.md" else located / "SKILL.md")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def phrase_text(phrase: tuple[str, ...]) -> str:
    return " ".join(phrase)


def mentions(text: str, name: str) -> bool:
    return re.search(rf"(?<![A-Za-z0-9-]){re.escape(name)}(?![A-Za-z0-9-])", text) is not None


def empty_summary() -> dict[str, int]:
    return {key: 0 for key in TYPES}


def routing_note_for(kind: str, names: tuple[str, ...]) -> str | None:
    if kind == "responsibility_overlap" and names == ("agent-browser", "ego-browser"):
        return EGO_BROWSER_ROUTING_NOTE
    return None


class Report:
    def __init__(self) -> None:
        self.findings: list[dict] = []
        self._seen: set[tuple] = set()

    def add(self, kind: str, skills: list[str], evidence: list[str], boundary: bool = False) -> None:
        names = tuple(sorted({name for name in skills if name}))
        proof = tuple(evidence[:6])
        key = (kind, names, proof, boundary)
        if not names or key in self._seen:
            return
        self._seen.add(key)
        self.findings.append(
            {
                "type": kind,
                "skills": list(names),
                "evidence": list(proof),
                "boundary_stated": boundary,
                "routing_note": routing_note_for(kind, names),
            }
        )


def profile_skill(skill: dict, repo_root: Path) -> dict:
    path = None if is_bundle(skill) else skill_markdown(skill, repo_root)
    text = read_text(path) if path is not None else ""
    front = frontmatter(text)
    body = text.split("---", 2)[2] if text.startswith("---") and text.count("---") >= 2 else text
    description = description_of(front)
    claimed = claim_phrases(description) | claim_phrases(owns_text(body))
    rejected = rejection_phrases(description + "\n" + body)
    claimed -= rejected
    digest = ""
    resolved = None
    if path is not None and path.is_file():
        resolved = resolve_file(path)
        try:
            digest = inventory.sha256_file(path)
        except OSError:
            digest = ""
    depends = metadata_names(front, "depends") + metadata_names(front, "requires")
    depends.extend(DEP_RE.findall(text))
    return {
        "name": skill.get("name") if isinstance(skill.get("name"), str) else "",
        "scope": skill.get("scope") if isinstance(skill.get("scope"), str) else "",
        "status": skill.get("status") if isinstance(skill.get("status"), str) else "",
        "bundle": is_bundle(skill),
        "text": text,
        "description": description,
        "front_name": inventory.skill_name_from_markdown(text) or "",
        "claims": claimed,
        "rejections": rejected,
        "triggers": trigger_tokens(description),
        "depends": list(dict.fromkeys(depends)),
        "constraints": skill.get("compatibility_constraints") if isinstance(skill.get("compatibility_constraints"), list) else [],
        "path": resolved,
        "digest": digest,
        "skill": skill,
    }


def installations(profile: dict, repo_root: Path) -> list[dict]:
    if profile["bundle"] or not profile["name"]:
        return []
    rows = []
    skill = profile["skill"]
    authority = skill.get("authority") if isinstance(skill.get("authority"), dict) else {}
    candidates: list[Path] = []
    canonical = authority.get("canonical_path")
    if isinstance(canonical, str) and is_concrete(canonical):
        candidates.append(repo_root / canonical / "SKILL.md")
    for value in skill.get("visible_paths") or []:
        if not is_concrete(value):
            continue
        located = inventory.locate_path(value, repo_root)
        candidates.append(located if located.name == "SKILL.md" else located / "SKILL.md")
    seen: set[Path] = set()
    for candidate in candidates:
        if not candidate.is_file():
            continue
        resolved = resolve_file(candidate)
        if resolved in seen:
            continue
        seen.add(resolved)
        basename = resolved.parent.name
        if basename in IGNORE_BASENAMES:
            continue
        try:
            digest = inventory.sha256_file(candidate)
        except OSError:
            continue
        rows.append(
            {
                "skill": profile["name"],
                "scope": profile["scope"],
                "basename": basename,
                "path": resolved,
                "digest": digest,
                "rank": SCOPE_RANK.get(profile["scope"], 0),
            }
        )
    return rows


def discover_copies(roots: list[Path]) -> list[dict]:
    copies = []
    for root in roots:
        if not root.is_dir():
            continue
        children = [root] if (root / "SKILL.md").is_file() else []
        try:
            children.extend(child for child in sorted(root.iterdir()) if child.is_dir())
        except OSError:
            continue
        for child in children:
            markdown = child / "SKILL.md"
            if not markdown.is_file():
                continue
            text = read_text(markdown)
            name = inventory.skill_name_from_markdown(text)
            if not name:
                continue
            try:
                digest = inventory.sha256_file(markdown)
            except OSError:
                continue
            copies.append({"name": name, "path": resolve_file(markdown), "digest": digest})
    return copies


def add_name_collisions(report: Report, profiles: list[dict], copies: list[dict], installs: list[dict]) -> None:
    by_inventory: dict[str, list[str]] = {}
    for index, profile in enumerate(profiles):
        if not profile["name"]:
            continue
        by_inventory.setdefault(profile["name"], []).append(f"skills[{index}]")
    for name, rows in sorted(by_inventory.items()):
        if len(rows) > 1:
            report.add("name_collision", [name], [f"inventory name {name} is repeated at {', '.join(rows)}"])

    recorded: dict[str, set[Path]] = {}
    for row in installs:
        recorded.setdefault(row["skill"], set()).add(row["path"])
    grouped: dict[str, list[dict]] = {}
    for profile in profiles:
        if profile["bundle"] or not profile["front_name"] or not profile["path"] or not profile["digest"]:
            continue
        grouped.setdefault(profile["front_name"], []).append(
            {"skill": profile["name"], "path": profile["path"], "digest": profile["digest"]}
        )
    for copy in copies:
        if copy["path"] in recorded.get(copy["name"], set()):
            continue
        grouped.setdefault(copy["name"], []).append(
            {"skill": copy["name"], "path": copy["path"], "digest": copy["digest"]}
        )
    for name, rows in sorted(grouped.items()):
        unique: list[dict] = []
        seen_paths: set[Path] = set()
        for row in rows:
            if row["path"] in seen_paths:
                continue
            seen_paths.add(row["path"])
            unique.append(row)
        digests = {row["digest"] for row in unique}
        if len(unique) < 2 or len(digests) < 2:
            continue
        evidence = [
            f"{row['path']} sha256:{row['digest'][:12]}"
            for row in unique[:4]
        ]
        evidence.insert(0, f"SKILL.md name {name} has different content")
        report.add("name_collision", [row["skill"] for row in unique], evidence)


def add_scope_shadows(report: Report, installs: list[dict]) -> None:
    grouped: dict[str, list[dict]] = {}
    for row in installs:
        grouped.setdefault(row["basename"], []).append(row)
    for basename, rows in sorted(grouped.items()):
        unique: list[dict] = []
        seen_paths: set[Path] = set()
        for row in rows:
            if row["path"] in seen_paths:
                continue
            seen_paths.add(row["path"])
            unique.append(row)
        if len({row["digest"] for row in unique}) < 2:
            continue
        ranked = [row for row in unique if row["rank"] > 0]
        for broader in ranked:
            for narrower in ranked:
                if broader["skill"] == narrower["skill"] or broader["rank"] <= narrower["rank"]:
                    continue
                report.add(
                    "scope_shadowing",
                    [broader["skill"], narrower["skill"]],
                    [
                        f"directory {basename}",
                        f"{broader['skill']} scope {broader['scope']} rank {broader['rank']} at {broader['path']}",
                        f"{narrower['skill']} scope {narrower['scope']} rank {narrower['rank']} at {narrower['path']}",
                    ],
                )


def name_prefix(name: str) -> str:
    return name.split("-", 1)[0]


def add_claim_conflicts(report: Report, profiles: list[dict]) -> None:
    usable = [profile for profile in profiles if profile["name"] and not profile["bundle"]]
    owners: dict[tuple[str, ...], set[str]] = {}
    for profile in usable:
        for phrase in profile["claims"]:
            owners.setdefault(phrase, set()).add(profile["name"])

    def distinctive(phrase: tuple[str, ...]) -> bool:
        return phrase not in GENERIC_PHRASES and len(owners.get(phrase, ())) < 3

    for index, left in enumerate(usable):
        for right in usable[index + 1 :]:
            shared = {phrase for phrase in left["claims"] & right["claims"] if distinctive(phrase)}
            boundary = mentions(left["text"], right["name"]) or mentions(right["text"], left["name"])
            if shared:
                report.add(
                    "responsibility_overlap",
                    [left["name"], right["name"]],
                    [f"shared claim \"{phrase_text(phrase)}\"" for phrase in sorted(shared)[:4]],
                    boundary,
                )
            related = bool(shared) or (
                name_prefix(left["name"]) == name_prefix(right["name"]) and name_prefix(left["name"]) not in {"skill", "skills"}
            )
            contradicted = []
            if related:
                for phrase in sorted(left["claims"] & right["rejections"]):
                    if phrase in GENERIC_PHRASES or mentions(right["text"], left["name"]):
                        continue
                    contradicted.append(f"{right['name']} rejects \"{phrase_text(phrase)}\" claimed by {left['name']}")
                for phrase in sorted(right["claims"] & left["rejections"]):
                    if phrase in GENERIC_PHRASES or mentions(left["text"], right["name"]):
                        continue
                    contradicted.append(f"{left['name']} rejects \"{phrase_text(phrase)}\" claimed by {right['name']}")
            if contradicted:
                report.add("behavior_divergence", [left["name"], right["name"]], contradicted)
            if left["front_name"] and left["front_name"] == right["front_name"] and left["digest"] and right["digest"] and left["digest"] != right["digest"]:
                report.add(
                    "behavior_divergence",
                    [left["name"], right["name"]],
                    [f"same SKILL.md name {left['front_name']} with different content"],
                )
            both = left["triggers"] and right["triggers"]
            if not both:
                continue
            inter = left["triggers"] & right["triggers"]
            union = left["triggers"] | right["triggers"]
            if len(inter) >= 4 and len(inter) / len(union) >= 0.45:
                report.add(
                    "trigger_ambiguity",
                    [left["name"], right["name"]],
                    ["shared trigger tokens: " + ", ".join(sorted(inter)[:8])],
                    boundary,
                )


def add_dependency_conflicts(report: Report, profiles: list[dict]) -> None:
    known = {profile["name"]: profile for profile in profiles if profile["name"]}
    edges: dict[str, set[str]] = {name: set() for name in known}
    for profile in profiles:
        if not profile["name"]:
            continue
        pins: dict[str, str] = {}
        for item in profile["constraints"]:
            if not isinstance(item, dict):
                continue
            kind = item.get("kind")
            value = item.get("value")
            if kind not in PIN_KINDS or not isinstance(value, str):
                continue
            previous = pins.get(kind)
            if previous and previous != value:
                report.add(
                    "dependency_conflict",
                    [profile["name"]],
                    [f"{kind} pins {previous!r} and {value!r}"],
                )
            pins[kind] = value
        for target in profile["depends"]:
            edges[profile["name"]].add(target)
            other = known.get(target)
            if other is None:
                report.add(
                    "dependency_conflict",
                    [profile["name"]],
                    [f"depends on {target}, which is not in the inventory"],
                )
            elif other["status"] in {"retired", "quarantined"}:
                report.add(
                    "dependency_conflict",
                    [profile["name"], target],
                    [f"{target} status is {other['status']}"],
                )

    visiting: set[str] = set()
    visited: set[str] = set()

    def walk(name: str, stack: list[str]) -> None:
        if name in visited or name not in edges:
            return
        visiting.add(name)
        for target in sorted(edges[name]):
            if target in visiting:
                cycle = stack[stack.index(target) :] + [target]
                report.add(
                    "dependency_conflict",
                    cycle[:-1],
                    ["dependency cycle " + " -> ".join(cycle)],
                )
                continue
            if target in known:
                walk(target, stack + [target])
        visiting.remove(name)
        visited.add(name)

    for name in sorted(edges):
        walk(name, [name])


def build_report(document: dict, repo_root: Path, scan_roots: list[Path], inventory_path: Path | None = None) -> dict:
    skills = document.get("skills") if isinstance(document.get("skills"), list) else []
    profiles = [profile_skill(skill, repo_root) for skill in skills if isinstance(skill, dict)]
    installs = [row for profile in profiles for row in installations(profile, repo_root)]
    report = Report()
    add_name_collisions(report, profiles, discover_copies(scan_roots), installs)
    add_scope_shadows(report, installs)
    add_claim_conflicts(report, profiles)
    add_dependency_conflicts(report, profiles)
    findings = sorted(report.findings, key=lambda item: (TYPES.index(item["type"]), item["skills"], item["evidence"]))
    summary = empty_summary()
    for finding in findings:
        summary[finding["type"]] += 1
    return {
        "report": REPORT_NAME,
        "read_only": True,
        "inventory": str(inventory_path) if inventory_path else None,
        "note": (
            "Name collision is one name with different SKILL.md content. "
            "The other types use responsibility claims, rejected claims, scope rank, "
            "dependency edges, and Use-when tokens. This command does not write "
            "conflict_status or select which skill runs."
        ),
        "summary": summary,
        "findings": findings,
    }


def render_text(report: dict) -> str:
    summary = report["summary"]
    lines = [
        "Conflict Report",
        f"Inventory: {report['inventory']}",
        "Read-only: yes",
        "Summary: " + " ".join(f"{key}={summary[key]}" for key in TYPES),
        report["note"],
    ]
    if not report["findings"]:
        lines.append("Findings: none")
    for finding in report["findings"]:
        lines.append(f"- type: {finding['type']}")
        lines.append("  skills: " + ", ".join(finding["skills"]))
        lines.append("  boundary_stated: " + ("yes" if finding["boundary_stated"] else "no"))
        if finding.get("routing_note"):
            lines.append(f"  routing_note: {finding['routing_note']}")
        for item in finding["evidence"]:
            lines.append(f"  evidence: {item}")
    return "\n".join(lines) + "\n"


def machine_roots(repo_root: Path, extra: list[Path], include_defaults: bool) -> list[Path]:
    roots: list[Path] = []
    if include_defaults:
        roots.extend(DEFAULT_MACHINE_ROOTS)
    roots.extend(extra)
    roots.extend(repo_root / relative for relative in (".agents/skills", ".codex/skills"))
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
    parser = argparse.ArgumentParser(description="Read-only skills conflict report")
    parser.add_argument("--inventory", type=Path, default=inventory.DEFAULT_INVENTORY)
    parser.add_argument("--repo-root", type=Path, default=inventory.DEFAULT_REPO_ROOT)
    parser.add_argument("--scan-root", action="append", default=[])
    parser.add_argument("--no-default-roots", action="store_true")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)
    inventory_path = args.inventory.expanduser()
    repo_root = args.repo_root.expanduser().resolve()
    try:
        document = json.loads(inventory_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"conflict report failed: {exc}", file=sys.stderr)
        return 1
    if not isinstance(document, dict):
        print("conflict report failed: inventory must be a JSON object", file=sys.stderr)
        return 1
    roots = machine_roots(
        repo_root,
        [path.expanduser() for path in args.scan_root],
        include_defaults=not args.no_default_roots,
    )
    return emit(build_report(document, repo_root, roots, inventory_path), args.format, args.output)


if __name__ == "__main__":
    sys.exit(main())
