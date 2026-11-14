"""Historical metadata only. Never open a file under a memory root."""
from .common import UUID, directory_names, walk_metadata, iso
from ..validation import timestamp


def collect(ctx):
    root = ctx.roots.get("memory")
    names, state = directory_names(root, ctx.policy)
    rows = []
    for bot in sorted(set(names) | ctx.bots):
        if not UUID.fullmatch(bot):
            state = "partial"
            continue
        files, status = walk_metadata(root / bot, ctx.policy) if root else ([], "not_collected")
        latest = max((row["mtime"] for row in files), default=None)
        rows.append({"id": "memory:agent_memory/" + bot, "scope": "agent_memory", "agent_id": bot,
                     "file_count": len(files) if status == "ok" else None,
                     "total_bytes": sum(row["bytes"] for row in files) if status == "ok" else None,
                     "latest_mtime": latest,
                     "snapshot_age_days": max(0, (ctx.now - timestamp(latest)).total_seconds() / 86400) if latest else None,
                     "evidence_level": "historical"})
        if status != "ok" and root:
            state = "partial"
    return rows, state
