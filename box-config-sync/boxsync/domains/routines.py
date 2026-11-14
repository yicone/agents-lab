"""Disk routine allowlist and per-bot turn placeholders (D6)."""
import hashlib
from ..hashing import config_fingerprint
from .common import UUID, directory_names, json_file, label, safe_id, safe_timestamp, walk_metadata


def turn_placeholder(bot):
    return {"id": "grok-routine-live:" + bot + "/not-collected", "kind": "turn",
            "owner_id": bot, "routine_id": "not-collected", "name": None, "enabled": None,
            "routine_sha256": None, "evidence_level": "not_collected"}


def collect(ctx):
    root = ctx.roots.get("routines")
    bots, state = directory_names(root, ctx.policy)
    rows = []
    for bot in bots:
        if not UUID.fullmatch(bot):
            state = "partial"
            continue
        files, status = walk_metadata(root / bot, ctx.policy)
        if status != "ok":
            state = "partial"
        for file in files:
            if "/" in file["path"] or not file["path"].endswith(".json"):
                continue
            data, status = json_file(root / bot, file["path"], ctx.policy)
            if status != "observed" or not safe_id(data.get("id")):
                state = "partial"
                continue
            item = {"name": label(data.get("name")), "enabled": data.get("enabled") if type(data.get("enabled")) is bool else None,
                    "schedule": label(data.get("schedule"), 120),
                    "prompt_sha256": hashlib.sha256(data["prompt"].encode()).hexdigest() if isinstance(data.get("prompt"), str) else None}
            rows.append({"id": "grok-routine:" + bot + "/" + data["id"], "kind": "disk",
                         "owner_id": bot, "routine_id": data["id"], **item,
                         "routine_sha256": config_fingerprint({**item, "updated_at": safe_timestamp(data.get("updated_at"))}),
                         "evidence_level": "observed"})
    rows.extend(turn_placeholder(bot) for bot in sorted(ctx.bots))
    return rows, state
