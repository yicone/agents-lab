"""Cloud skill metadata; body bytes exist only during a bounded read."""
from ..multica_cli import CliError
import re
import hashlib
from ..secrets_scan import scan
from ..policy import MAX_FILE_BYTES, MAX_TREE_ENTRIES
from ..validation import load_schema, validate, InvalidDocument
from .common import UUID, label, safe_timestamp
from .multica_agents import records, assert_scope


def body_fields(state="uncollected", size=None):
    return {"body_state": state, "body_sha256": None, "body_bytes": size,
            "files": [], "files_truncated": False, "collected_at": None}


def inspect_content(data, workspace, skill, collected_at):
    """Return schema-safe metadata and ephemeral preview texts, never raw errors."""
    assert_scope(data, workspace)
    if data.get("id") != skill:
        raise CliError()
    result = body_fields()
    result["collected_at"] = collected_at
    content_hash = data.get("content_hash")
    if isinstance(content_hash, str) and re.fullmatch("[0-9a-fA-F]{64}", content_hash):
        result["api_content_hash"] = content_hash.lower()
    texts = {}
    files = data.get("files", [])
    if not isinstance(files, list):
        raise CliError()
    # SKILL.md is the top-level content; files may also include that path.
    if "content" in data:
        files = [{"path": "SKILL.md", "content": data["content"]}] + [
            f for f in files if not isinstance(f, dict) or f.get("path") != "SKILL.md"]
    # Keep the primary document selectable even when an oversized file list
    # puts it after the tree display limit.
    files = [f for f in files if isinstance(f, dict) and f.get("path") == "SKILL.md"] + [
        f for f in files if not isinstance(f, dict) or f.get("path") != "SKILL.md"]
    result["files_truncated"] = len(files) > MAX_TREE_ENTRIES
    seen = set()
    for entry in files[:MAX_TREE_ENTRIES]:
        if not isinstance(entry, dict):
            raise CliError()
        path = entry.get("path")
        if (not isinstance(path, str) or not path or len(path) > 512 or path.startswith("/")
                or "\\" in path or any(x in ("", ".", "..") for x in path.split("/"))
                or any(ord(c) < 32 for c in path) or scan(path) or path in seen):
            raise CliError()
        seen.add(path)
        text = entry.get("content")
        meta = {"path": path, "bytes": None, "sha256": None, "state": "error"}
        if isinstance(text, str):
            raw = text.encode("utf-8")
            meta["bytes"] = len(raw)
            if len(raw) > MAX_FILE_BYTES:
                meta["state"] = "too_large"
            else:
                meta.update(state="observed", sha256=hashlib.sha256(raw).hexdigest())
                findings = scan(text)
                if not findings and not any(ord(c) < 32 and c not in "\n\r\t\f" for c in text):
                    texts[path] = text
                if path == "SKILL.md":
                    result.update(body_state="withheld_secret" if findings else "observed" if raw else "empty",
                                  body_sha256=meta["sha256"], body_bytes=len(raw))
                    if findings:
                        result["secret_findings"] = findings[:100]
            if path == "SKILL.md" and meta["state"] == "too_large":
                result.update(body_state="too_large", body_bytes=len(raw))
        result["files"].append(meta)
    return result, texts


def read_body(cli, workspace, skill, collected_at):
    try:
        return inspect_content(cli.run(["skill", "get", skill, "--with-content"], workspace),
                               workspace, skill, collected_at)
    except CliError as exc:
        state = "denied" if exc.reason in ("auth", "denied") else "too_large" if exc.reason == "too_large" else "error"
        return {**body_fields(state), "collected_at": collected_at}, {}
    except (UnicodeError, ValueError, TypeError):
        return {**body_fields("error"), "collected_at": collected_at}, {}


def collect(ctx):
    if ctx.offline:
        return [], "not_collected"
    rows = []
    previous = {row["id"]: row for row in ctx.previous_cloud_skills}
    state = "ok"
    schema = load_schema("snapshot")
    for workspace in ctx.workspaces:
        if workspace in ctx.inaccessible_ids:
            state = "partial"
            continue
        try:
            skills = records(ctx.cli.run(["skill", "list"], workspace), "skills")
            pending = []
            for data in skills:
                assert_scope(data, workspace)
                skill = data.get("id") if isinstance(data, dict) else None
                if not isinstance(skill, str) or not UUID.fullmatch(skill):
                    raise CliError()
                row = {"id": "multica-skill:" + workspace + "/" + skill,
                                "workspace_id": workspace, "skill_id": skill, "name": label(data.get("name")),
                                "updated_at": safe_timestamp(data.get("updated_at")),
                                "api_content_hash": data["content_hash"].lower() if isinstance(data.get("content_hash"), str)
                                    and re.fullmatch("[0-9a-fA-F]{64}", data["content_hash"]) else None,
                                "body_state": "uncollected", "body_sha256": None, "body_bytes": None,
                                "files": [], "files_truncated": False, "collected_at": None, "agents": []}
                bindings = data.get("agents", [])
                if not isinstance(bindings, list) or len(bindings) > 500:
                    raise CliError()
                for binding in bindings:
                    if not isinstance(binding, dict):
                        raise CliError()
                    agent = binding.get("agent_id", binding.get("id"))
                    if not isinstance(agent, str) or not UUID.fullmatch(agent):
                        raise CliError()
                    row["agents"].append({"agent_id": agent, "enabled": binding.get("enabled")
                                          if type(binding.get("enabled")) is bool else None})
                old = previous.get(row["id"])
                if old:
                    try:
                        validate(old, schema["$defs"]["cloudSkill"], schema)
                    except InvalidDocument:
                        old = None
                if old and row["updated_at"] and old["updated_at"] == row["updated_at"] and old["body_sha256"]:
                    if row["api_content_hash"] is None:
                        row["api_content_hash"] = old["api_content_hash"]
                    for key in body_fields():
                        row[key] = old[key]
                    if "secret_findings" in old:
                        row["secret_findings"] = old["secret_findings"]
                else:
                    fields, _ = read_body(ctx.cli, workspace, skill, ctx.now.isoformat().replace("+00:00", "Z"))
                    row.update(fields)
                if row["body_state"] == "denied":
                    ctx.inaccessible(workspace, "auth")
                    state = "partial"
                pending.append(row)
                if workspace in ctx.inaccessible_ids:
                    break
            rows.extend(pending)
        except CliError as exc:
            ctx.inaccessible(workspace, exc.reason)
            state = "partial"
    return rows, state
