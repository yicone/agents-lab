#!/usr/bin/env python3
"""Git source of truth for Multica agent instructions and Grok Bot descriptions.

Scan is read-only. It hashes Git text the same way Atlas hashes instructions
(NFC, LF newlines, trimmed edges, SHA-256) and compares that with a collected
instructions_sha256 or Bot description hash. Equal hashes are aligned; different
hashes are modified.

Sync prints a line diff and does not call `multica agent update` unless --apply
is present. --apply backs up the live instructions first. Grok Bot text is never
written by this command.
"""
from __future__ import annotations

import argparse
import base64
import difflib
import hashlib
import json
import os
import re
import subprocess
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote

SCHEMA = "instructions-sources/v1"
MANIFEST_SCHEMA = "grok-bots-manifest/v1"
ROOT = Path(__file__).resolve().parents[1]
SLOT_KEY = "sand.client.slice.client-meta.account-slot"
ACCOUNT_PREFIX = "sand.client.slice.account."
ROSTER_SUFFIX = ".roster.last-roster"
FORBIDDEN_KEY = ("transcript", "composer-draft", "secret", "credential", "cookie")
_B32_ALPHABET = frozenset("abcdefghijklmnopqrstuvwxyz234567")
SECRET_PATTERNS = (
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{8,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)
Runner = Callable[[list[str]], str]
ApplyFn = Callable[[str, str, str], None]


def normalize_instructions(text: str) -> str:
    """Same canonical form as the Atlas collector: NFC, LF, trimmed edges."""
    text = unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n"))
    return text.strip()


def instructions_sha256(text: str | None) -> str | None:
    """SHA-256 of the normalized original. Empty text has no hash."""
    if not isinstance(text, str):
        return None
    norm = normalize_instructions(text)
    if not norm:
        return None
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def contains_secret(text: str) -> bool:
    return any(pattern.search(text) for pattern in SECRET_PATTERNS)


def default_backup_dir() -> Path:
    return Path.home() / ".local" / "share" / "agents-lab" / "instructions-backup"


def default_roster_dir() -> Path:
    return Path.home() / "Library/Application Support/Grok Bot/sand-client-persistence"


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_index(root: Path) -> dict[str, Any]:
    path = root / "sources" / "index.json"
    if not path.is_file():
        return {"schema": SCHEMA, "agents": [], "bots": []}
    data = _read_json(path)
    if not isinstance(data, dict):
        raise SystemExit(f"{path} is not an object")
    data.setdefault("agents", [])
    data.setdefault("bots", [])
    return data


def load_manifest(root: Path) -> dict[str, Any]:
    path = root / "grok-bots-manifest.json"
    if not path.is_file():
        return {"schema": MANIFEST_SCHEMA, "bots": []}
    data = _read_json(path)
    if not isinstance(data, dict):
        raise SystemExit(f"{path} is not an object")
    data.setdefault("bots", [])
    return data


def read_source_text(root: Path, relative: str | None) -> str:
    if not relative:
        return ""
    path = (root / "sources" / relative).resolve()
    sources = (root / "sources").resolve()
    if path != sources and sources not in path.parents:
        raise SystemExit(f"source path escapes sources/: {relative}")
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def git_agents(root: Path) -> list[dict[str, Any]]:
    rows = []
    for item in load_index(root).get("agents") or []:
        if not isinstance(item, dict) or not item.get("id") or not item.get("workspace_id"):
            continue
        text = "" if item.get("withheld") else read_source_text(root, item.get("path"))
        rows.append({**item, "text": text, "sha256": None if item.get("withheld") else instructions_sha256(text)})
    return rows


def git_bots(root: Path) -> list[dict[str, Any]]:
    skills_of = {}
    for item in load_manifest(root).get("bots") or []:
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            skills = item.get("skills") if isinstance(item.get("skills"), list) else []
            skills_of[item["id"]] = [name for name in skills if isinstance(name, str) and name.strip()]
    rows = []
    for item in load_index(root).get("bots") or []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        text = "" if item.get("withheld") else read_source_text(root, item.get("path"))
        rows.append(
            {
                **item,
                "text": text,
                "sha256": None if item.get("withheld") else instructions_sha256(text),
                "skills": skills_of.get(item["id"], []),
            }
        )
    return rows


def _items(payload: Any, key: str) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        raw = payload.get(key)
        if isinstance(raw, list):
            return [item for item in raw if isinstance(item, dict)]
    raise SystemExit(f"collected {key} JSON must be a list or an object with {key}")


def live_hash(record: dict[str, Any], hash_key: str, text_key: str) -> str | None:
    value = record.get(hash_key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    text = record.get(text_key)
    return instructions_sha256(text if isinstance(text, str) else None)


def compare_agents(git_rows: list[dict[str, Any]], live_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    live_by = {(row.get("workspace_id"), row.get("id")): row for row in live_rows if row.get("id") and row.get("workspace_id")}
    seen = set()
    result = []
    for git in git_rows:
        key = (git.get("workspace_id"), git.get("id"))
        seen.add(key)
        live = live_by.get(key)
        result.append(_agent_row(git, live))
    for key, live in live_by.items():
        if key in seen:
            continue
        result.append(_agent_row(None, live))
    return result


def _agent_row(git: dict[str, Any] | None, live: dict[str, Any] | None) -> dict[str, Any]:
    has_git = git is not None
    has_live = live is not None
    git = git or {}
    live = live or {}
    if git.get("withheld"):
        status = "withheld"
    elif not has_git:
        status = "untracked"
    elif not has_live:
        status = "missing"
    else:
        git_hash = git.get("sha256")
        collected = live_hash(live, "instructions_sha256", "instructions")
        status = "aligned" if git_hash == collected else "modified"
    live_text = live.get("instructions") if isinstance(live.get("instructions"), str) else ""
    return {
        "kind": "agent",
        "workspace_id": git.get("workspace_id") or live.get("workspace_id"),
        "workspace_slug": git.get("workspace_slug") or live.get("workspace_slug"),
        "id": git.get("id") or live.get("id"),
        "name": git.get("name") or live.get("name"),
        "status": status,
        "git_sha256": git.get("sha256"),
        "live_sha256": live_hash(live, "instructions_sha256", "instructions") if live else None,
        "git_text": git.get("text") or "",
        "live_text": live_text,
    }


def compare_bots(git_rows: list[dict[str, Any]], live_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    live_by = {row.get("id"): row for row in live_rows if row.get("id")}
    seen = set()
    result = []
    for git in git_rows:
        seen.add(git.get("id"))
        result.append(_bot_row(git, live_by.get(git.get("id"))))
    for bot_id, live in live_by.items():
        if bot_id in seen:
            continue
        result.append(_bot_row(None, live))
    return result


def _bot_row(git: dict[str, Any] | None, live: dict[str, Any] | None) -> dict[str, Any]:
    has_git = git is not None
    has_live = live is not None
    git = git or {}
    live = live or {}
    if git.get("withheld"):
        status = "withheld"
    elif not has_git:
        status = "untracked"
    elif not has_live:
        status = "missing"
    else:
        collected = live_hash(live, "description_sha256", "description")
        status = "aligned" if git.get("sha256") == collected else "modified"
    live_text = live.get("description") if isinstance(live.get("description"), str) else ""
    return {
        "kind": "bot",
        "id": git.get("id") or live.get("id"),
        "name": git.get("name") or live.get("name"),
        "status": status,
        "git_sha256": git.get("sha256"),
        "live_sha256": live_hash(live, "description_sha256", "description") if live else None,
        "git_text": git.get("text") or "",
        "live_text": live_text,
        "skills": git.get("skills") or [],
    }


def line_diff(git_text: str, live_text: str, label: str) -> str:
    git_lines = normalize_instructions(git_text).splitlines()
    live_lines = normalize_instructions(live_text).splitlines()
    diff = difflib.unified_diff(
        git_lines,
        live_lines,
        fromfile=f"git/{label}",
        tofile=f"live/{label}",
        lineterm="",
    )
    return "\n".join(diff)


def summarize(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"aligned": 0, "modified": 0, "untracked": 0, "missing": 0, "withheld": 0}
    for row in rows:
        status = row.get("status")
        if status in counts:
            counts[status] += 1
    return counts


def build_report(root: Path, agents: list[dict[str, Any]], bots: list[dict[str, Any]]) -> dict[str, Any]:
    agent_rows = compare_agents(git_agents(root), agents)
    bot_rows = compare_bots(git_bots(root), bots)
    return {
        "schema": "instructions-drift/v1",
        "agents": agent_rows,
        "bots": bot_rows,
        "summary": {"agents": summarize(agent_rows), "bots": summarize(bot_rows)},
    }


def format_report(report: dict[str, Any], *, diffs: bool) -> str:
    lines = []
    for kind in ("agents", "bots"):
        counts = report["summary"].get(kind)
        if not counts:
            continue
        lines.append(
            f"{kind}: aligned {counts['aligned']}, modified {counts['modified']}, "
            f"untracked {counts['untracked']}, missing {counts['missing']}, withheld {counts['withheld']}"
        )
    for row in report.get("inaccessible_workspaces") or []:
        lines.append(f"inaccessible workspace {row.get('slug') or row.get('workspace_id')}")
    if diffs:
        rows = list(report.get("agents") or []) + list(report.get("bots") or [])
        for row in rows:
            if row["status"] != "modified":
                continue
            label = row.get("workspace_slug") or row.get("workspace_id") or "bot"
            name = row.get("name") or row.get("id")
            lines.append(f"\n# {row['kind']} {label}/{name} modified")
            diff = line_diff(row.get("git_text") or "", row.get("live_text") or "", f"{label}/{name}")
            lines.append(diff or "(哈希不一致，规范化正文相同)")
    return "\n".join(lines) + "\n"


def backup_instructions(backup_dir: Path, workspace_id: str, agent_id: str, text: str, now: datetime) -> Path:
    stamp = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = backup_dir / stamp / workspace_id / f"{agent_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text if isinstance(text, str) else "", encoding="utf-8")
    if not path.is_file():
        raise OSError(f"backup was not written: {path}")
    return path


def sync_agents(report: dict[str, Any], *, apply: bool, backup_dir: Path, apply_fn: ApplyFn, now: datetime | None = None) -> list[dict[str, Any]]:
    """Print-only unless apply is true. Backup happens before the write call."""
    now = now or datetime.now(timezone.utc)
    results = []
    for row in report["agents"]:
        if row["status"] != "modified":
            continue
        diff = line_diff(row.get("git_text") or "", row.get("live_text") or "", row.get("name") or row["id"])
        item = {
            "workspace_id": row["workspace_id"],
            "id": row["id"],
            "name": row.get("name"),
            "diff": diff,
            "applied": False,
            "backup": None,
        }
        if apply:
            backup = backup_instructions(backup_dir, row["workspace_id"], row["id"], row.get("live_text") or "", now)
            item["backup"] = str(backup)
            apply_fn(row["workspace_id"], row["id"], normalize_instructions(row.get("git_text") or ""))
            item["applied"] = True
        results.append(item)
    return results


def decode_blob_name(name: str) -> str | None:
    if not name.endswith(".blob"):
        return None
    stem = name[:-5]
    if not stem or any(ch not in _B32_ALPHABET for ch in stem):
        return None
    pad = "=" * ((8 - len(stem) % 8) % 8)
    try:
        raw = base64.b32decode(stem.upper() + pad, casefold=True)
        return raw.decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None


def slice_kind(key: str) -> str | None:
    lowered = unquote(key).lower()
    if any(part in lowered for part in FORBIDDEN_KEY):
        return None
    if key == SLOT_KEY:
        return "slot"
    if key.startswith(ACCOUNT_PREFIX) and key.endswith(ROSTER_SUFFIX):
        auth = key[len(ACCOUNT_PREFIX) : -len(ROSTER_SUFFIX)]
        if auth and "." not in auth and "/" not in auth and "\\" not in auth:
            return "roster"
    return None


def iter_allowed_blobs(directory: Path) -> list[tuple[str, str, Path]]:
    found = []
    try:
        names = list(directory.iterdir())
    except OSError:
        return found
    for path in names:
        try:
            if not path.is_file():
                continue
        except OSError:
            continue
        key = decode_blob_name(path.name)
        if key is None:
            continue
        kind = slice_kind(key)
        if kind is None:
            continue
        found.append((key, kind, path))
    return found


def _unwrap(payload: Any) -> Any:
    if isinstance(payload, dict) and "value" in payload:
        return payload.get("value")
    return payload


def read_roster_descriptions(directory: Path, reader: Callable[[Path], str] | None = None) -> list[dict[str, Any]]:
    """Read id, name, and description from the allow-listed roster slice only."""
    read = reader or (lambda path: path.read_text(encoding="utf-8"))
    if not directory.is_dir():
        return []
    files = iter_allowed_blobs(directory)
    slot_path = next((path for key, kind, path in files if kind == "slot"), None)
    if slot_path is None:
        return []
    slot = _unwrap(json.loads(read(slot_path)))
    if not isinstance(slot, str) or not slot.strip():
        return []
    auth = unquote(slot).strip()
    roster_path = None
    for key, kind, path in files:
        if kind != "roster":
            continue
        file_auth = key[len(ACCOUNT_PREFIX) : -len(ROSTER_SUFFIX)]
        if unquote(file_auth).strip() == auth:
            roster_path = path
            break
    if roster_path is None:
        return []
    roster = _unwrap(json.loads(read(roster_path)))
    rows = roster.get("rows") if isinstance(roster, dict) else None
    if not isinstance(rows, list):
        return []
    bots = []
    for raw in rows:
        if not isinstance(raw, dict) or not isinstance(raw.get("id"), str) or not raw["id"]:
            continue
        description = raw.get("description") if isinstance(raw.get("description"), str) else ""
        name = raw.get("name") if isinstance(raw.get("name"), str) else raw["id"]
        bots.append(
            {
                "id": raw["id"],
                "name": name.strip() or raw["id"],
                "description": description,
                "description_sha256": instructions_sha256(description),
            }
        )
    return bots


def _run_json(argv: list[str], run: Runner) -> Any:
    return json.loads(run(argv))


def collect_agents(run: Runner) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Read agents from every workspace this CLI can open.

    A workspace that rejects the token, or whose agent list stays on a different
    workspace, is reported and not copied under another slug.
    """
    workspaces = _run_json(["workspace", "list", "--output", "json"], run)
    if isinstance(workspaces, dict):
        workspaces = workspaces.get("workspaces") or workspaces.get("items") or []
    agents: list[dict[str, Any]] = []
    inaccessible: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for ws in workspaces:
        if not isinstance(ws, dict) or not ws.get("id"):
            continue
        try:
            listed = _run_json(["--workspace-id", ws["id"], "agent", "list", "--output", "json"], run)
        except SystemExit as exc:
            inaccessible.append({"workspace_id": ws["id"], "slug": str(ws.get("slug") or ""), "error": str(exc)})
            continue
        items = listed if isinstance(listed, list) else (listed.get("items") or listed.get("agents") or [])
        returned = {item.get("workspace_id") for item in items if isinstance(item, dict) and item.get("workspace_id")}
        if returned and ws["id"] not in returned:
            inaccessible.append(
                {
                    "workspace_id": ws["id"],
                    "slug": str(ws.get("slug") or ""),
                    "error": "agent list stayed on another workspace",
                }
            )
            continue
        for item in items:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            if item.get("archived_at") or item.get("archived") is True:
                continue
            workspace_id = item.get("workspace_id") or ws["id"]
            if workspace_id != ws["id"]:
                continue
            key = (workspace_id, item["id"])
            if key in seen:
                continue
            seen.add(key)
            text = item.get("instructions") if isinstance(item.get("instructions"), str) else ""
            agents.append(
                {
                    "workspace_id": workspace_id,
                    "workspace_slug": ws.get("slug") or workspace_id,
                    "workspace_name": ws.get("name") or ws.get("slug") or workspace_id,
                    "id": item["id"],
                    "name": item.get("name") or item["id"],
                    "instructions": text,
                    "instructions_sha256": instructions_sha256(text),
                }
            )
    return agents, inaccessible


def _safe_part(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", value).strip("._")
    return cleaned or "item"


def _write_text(root: Path, relative: str, text: str) -> None:
    path = root / "sources" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = normalize_instructions(text)
    path.write_text((normalized + "\n") if normalized else "", encoding="utf-8")


def seed_sources(root: Path, agents: list[dict[str, Any]], bots: list[dict[str, Any]]) -> dict[str, int]:
    """Write Git source files. Bodies that match a secret pattern are withheld."""
    existing_skills = {}
    manifest = load_manifest(root)
    for item in manifest.get("bots") or []:
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            skills = item.get("skills") if isinstance(item.get("skills"), list) else []
            existing_skills[item["id"]] = [name for name in skills if isinstance(name, str)]

    agent_index = []
    withheld = 0
    for agent in agents:
        slug = _safe_part(str(agent.get("workspace_slug") or agent["workspace_id"]))
        relative = f"agents/{slug}/{agent['id']}.md"
        text = agent.get("instructions") if isinstance(agent.get("instructions"), str) else ""
        record = {
            "workspace_id": agent["workspace_id"],
            "workspace_slug": agent.get("workspace_slug"),
            "workspace_name": agent.get("workspace_name"),
            "id": agent["id"],
            "name": agent.get("name"),
            "path": None,
        }
        if text and contains_secret(text):
            record["withheld"] = "secret-pattern"
            withheld += 1
        elif normalize_instructions(text):
            _write_text(root, relative, text)
            record["path"] = relative
        agent_index.append(record)

    bot_index = []
    manifest_bots = []
    for bot in bots:
        relative = f"bots/{_safe_part(bot['id'])}.md"
        text = bot.get("description") if isinstance(bot.get("description"), str) else ""
        record = {"id": bot["id"], "name": bot.get("name"), "path": None}
        if text and contains_secret(text):
            record["withheld"] = "secret-pattern"
            withheld += 1
        elif normalize_instructions(text):
            _write_text(root, relative, text)
            record["path"] = relative
        bot_index.append(record)
        manifest_bots.append({"id": bot["id"], "name": bot.get("name") or bot["id"], "skills": existing_skills.get(bot["id"], [])})

    _write_json(root / "sources" / "index.json", {"schema": SCHEMA, "agents": agent_index, "bots": bot_index})
    _write_json(
        root / "grok-bots-manifest.json",
        {
            "schema": MANIFEST_SCHEMA,
            "description": "Cloud skill names declared for each Grok Bot. Maintained in Git. This tool does not call the Grok client RPC.",
            "bots": manifest_bots,
        },
    )
    return {"agents": len(agent_index), "bots": len(bot_index), "withheld": withheld}


def export_ui(root: Path) -> dict[str, Any]:
    agents = {}
    for row in git_agents(root):
        agents[f"{row['workspace_id']}/{row['id']}"] = {
            "name": row.get("name"),
            "sha256": row.get("sha256"),
            "text": "" if row.get("withheld") else row.get("text") or "",
            "withheld": bool(row.get("withheld")),
        }
    bots = {}
    for row in git_bots(root):
        bots[row["id"]] = {
            "name": row.get("name"),
            "sha256": row.get("sha256"),
            "text": "" if row.get("withheld") else row.get("text") or "",
            "skills": row.get("skills") or [],
            "withheld": bool(row.get("withheld")),
        }
    return {"schema": "instruction-sources/v1", "agents": agents, "bots": bots}


def multica_runner(binary: str) -> Runner:
    def run(argv: list[str]) -> str:
        proc = subprocess.run([binary, *argv], check=False, capture_output=True, text=True)
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip().splitlines()
            tail = detail[-1] if detail else f"exit {proc.returncode}"
            raise SystemExit(f"multica {' '.join(argv[:3])} failed: {tail}")
        return proc.stdout

    return run


def multica_apply(binary: str) -> ApplyFn:
    def apply(workspace_id: str, agent_id: str, instructions: str) -> None:
        argv = [
            binary,
            "--workspace-id",
            workspace_id,
            "agent",
            "update",
            agent_id,
            f"--instructions={instructions}",
            "--output",
            "json",
        ]
        proc = subprocess.run(argv, check=False, capture_output=True, text=True)
        if proc.returncode != 0:
            raise SystemExit(f"multica agent update failed for {agent_id} (exit {proc.returncode})")

    return apply


def _load_live(args: argparse.Namespace, *, need_bots: bool) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, str]]]:
    inaccessible: list[dict[str, str]] = []
    if args.agents_json:
        agents = _items(_read_json(Path(args.agents_json)), "agents")
    else:
        agents, inaccessible = collect_agents(multica_runner(args.multica))
    bots_json = getattr(args, "bots_json", None)
    if bots_json:
        bots = _items(_read_json(Path(bots_json)), "bots")
    elif need_bots:
        bots = read_roster_descriptions(Path(args.roster_dir))
    else:
        bots = []
    return agents, bots, inaccessible


def cmd_scan(args: argparse.Namespace) -> int:
    agents, bots, inaccessible = _load_live(args, need_bots=True)
    report = build_report(Path(args.root), agents, bots)
    report["inaccessible_workspaces"] = inaccessible
    if args.format == "json":
        printable = {
            "schema": report["schema"],
            "summary": report["summary"],
            "inaccessible_workspaces": inaccessible,
            "agents": [_public_row(row) for row in report["agents"]],
            "bots": [_public_row(row) for row in report["bots"]],
        }
        sys.stdout.write(json.dumps(printable, ensure_ascii=False, indent=2) + "\n")
    else:
        sys.stdout.write(format_report(report, diffs=True))
    return 0


def _public_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key not in {"git_text", "live_text"}}


def cmd_sync(args: argparse.Namespace) -> int:
    if args.apply and args.dry_run:
        raise SystemExit("pass either --apply or --dry-run")
    agents, _bots, inaccessible = _load_live(args, need_bots=False)
    report = build_report(Path(args.root), agents, [])
    report["bots"] = []
    report["summary"].pop("bots", None)
    report["inaccessible_workspaces"] = inaccessible
    results = sync_agents(
        report,
        apply=bool(args.apply),
        backup_dir=Path(args.backup_dir),
        apply_fn=multica_apply(args.multica),
    )
    sys.stdout.write(format_report(report, diffs=False))
    if not results:
        sys.stdout.write("sync: no modified agents\n")
        return 0
    for item in results:
        name = item.get("name") or item["id"]
        sys.stdout.write(f"\n# agent {name}\n")
        sys.stdout.write((item.get("diff") or "(哈希不一致，规范化正文相同)") + "\n")
        if item.get("applied"):
            sys.stdout.write(f"applied backup={item['backup']}\n")
        else:
            sys.stdout.write("dry-run: multica agent update was not called\n")
    return 0


def cmd_capture(args: argparse.Namespace) -> int:
    agents, inaccessible = collect_agents(multica_runner(args.multica))
    bots = read_roster_descriptions(Path(args.roster_dir))
    counts = seed_sources(Path(args.root), agents, bots)
    if args.ui_out:
        _write_json(Path(args.ui_out), export_ui(Path(args.root)))
    sys.stdout.write(
        f"captured agents {counts['agents']}, bots {counts['bots']}, withheld {counts['withheld']}, "
        f"inaccessible {len(inaccessible)}\n"
    )
    return 0


def cmd_export_ui(args: argparse.Namespace) -> int:
    _write_json(Path(args.out), export_ui(Path(args.root)))
    sys.stdout.write(f"wrote {args.out}\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", default=str(ROOT), help="instructions-governance directory")
    parser = argparse.ArgumentParser(
        parents=[common],
        description="Compare Git instruction sources with collected Multica agents and Grok Bot descriptions.",
    )
    sub = parser.add_subparsers(dest="command")

    def add_live(cmd: argparse.ArgumentParser, *, bots: bool) -> None:
        cmd.add_argument("--agents-json", help="collected agents JSON instead of the Multica CLI")
        cmd.add_argument("--multica", default=os.environ.get("MULTICA_BIN", "multica"))
        if bots:
            cmd.add_argument("--bots-json", help="collected bots JSON instead of the local roster")
            cmd.add_argument("--roster-dir", default=str(default_roster_dir()))

    scan = sub.add_parser("scan", parents=[common], help="read-only drift scan")
    add_live(scan, bots=True)
    scan.add_argument("--format", choices=("text", "json"), default="text")
    scan.set_defaults(func=cmd_scan)

    sync = sub.add_parser("sync", parents=[common], help="print Multica instruction diffs; write only with --apply")
    add_live(sync, bots=False)
    sync.add_argument("--dry-run", action="store_true", help="print the line diff and do not update agents")
    sync.add_argument("--apply", action="store_true", help="backup live instructions, then call multica agent update")
    sync.add_argument("--backup-dir", default=str(default_backup_dir()))
    sync.set_defaults(func=cmd_sync)

    capture = sub.add_parser("capture", parents=[common], help="read live instructions into this Git tree; does not update agents")
    capture.add_argument("--multica", default=os.environ.get("MULTICA_BIN", "multica"))
    capture.add_argument("--roster-dir", default=str(default_roster_dir()))
    capture.add_argument("--ui-out", help="also write the Atlas drawer catalog")
    capture.set_defaults(func=cmd_capture)

    export = sub.add_parser("export-ui", parents=[common], help="write the drawer catalog from Git sources")
    export.add_argument("--out", required=True)
    export.set_defaults(func=cmd_export_ui)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    raw = list(sys.argv[1:] if argv is None else argv)
    if not any(part in {"scan", "sync", "capture", "export-ui"} for part in raw):
        raw = ["scan", *raw]
    args = parser.parse_args(raw)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
