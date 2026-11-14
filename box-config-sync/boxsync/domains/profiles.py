"""Grok profile allowlist; description is used only for hashing."""
from ..hashing import config_fingerprint, instructions_sha256
from .common import UUID, directory_names, json_file, label

_PROFILE_FIELDS = ("name", "title", "description", "harness", "avatarColor", "avatarShape", "namedBy", "serverId")


def collect(ctx):
    root = ctx.roots.get("profiles")
    names, state = directory_names(root, ctx.policy)
    rows = []
    settings, _ = json_file(ctx.settings.parent, ctx.settings.name, ctx.policy) if ctx.settings else (None, None)
    notify = settings.get("notifyOnAgentUpdates") if settings else None
    for bot in names:
        if not UUID.fullmatch(bot):
            state = "partial"
            continue
        ctx.bots.add(bot)
        data, read_state = json_file(root, bot + "/profile.json", ctx.policy)
        valid = data is not None and all(data.get(k) is None or isinstance(data[k], str) for k in _PROFILE_FIELDS)
        if not valid:
            state = "partial"
            data = {}
        rows.append({
            "id": "grok-profile:" + bot, "bot_id": bot, "name": label(data.get("name")),
            "title": label(data.get("title")), "harness": label(data.get("harness"), 64),
            "description_sha256": instructions_sha256(data.get("description")),
            "profile_sha256": config_fingerprint({k: data[k] for k in _PROFILE_FIELDS if k in data}) if valid else None,
            "notify_on_agent_updates": notify if type(notify) is bool else None,
            "evidence_level": "observed" if valid else "unknown",
            "state": "observed" if valid else ("missing" if read_state == "absent" else "denied"),
        })
    return rows, state
