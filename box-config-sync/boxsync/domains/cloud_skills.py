"""Declarations only in P1-B. Content collection belongs to P1-D / ACM-895."""
from ..multica_cli import CliError
import re
from .common import UUID, label, safe_timestamp
from .multica_agents import records, assert_scope


def collect(ctx):
    if ctx.offline:
        return [], "not_collected"
    rows = []
    state = "ok"
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
                pending.append({"id": "multica-skill:" + workspace + "/" + skill,
                                "workspace_id": workspace, "skill_id": skill, "name": label(data.get("name")),
                                "updated_at": safe_timestamp(data.get("updated_at")),
                                "api_content_hash": data["content_hash"].lower() if isinstance(data.get("content_hash"), str)
                                    and re.fullmatch("[0-9a-fA-F]{64}", data["content_hash"]) else None,
                                "body_state": "uncollected", "body_sha256": None, "body_bytes": None,
                                "files": [], "files_truncated": False, "collected_at": None, "agents": []})
            rows.extend(pending)
        except CliError as exc:
            ctx.inaccessible(workspace, exc.reason)
            state = "partial"
    return rows, state
