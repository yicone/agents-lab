"""Attachment hashes and metadata, never binary content in a snapshot."""
import unicodedata
from ..policy import MAX_ATTACHMENT_BYTES
from .common import UUID, directory_names, label, walk_metadata


def collect(ctx):
    root = ctx.roots.get("attachments")
    bots, state = directory_names(root, ctx.policy)
    rows = []
    for bot in bots:
        if not UUID.fullmatch(bot):
            state = "partial"
            continue
        files, status = walk_metadata(root / bot, ctx.policy, content=True, max_bytes=MAX_ATTACHMENT_BYTES)
        if status != "ok":
            state = "partial"
        for file in files:
            path = unicodedata.normalize("NFC", file["path"])
            if label(path, 1024) is None:
                state = "partial"
                continue
            hash_state = file["state"]
            if hash_state not in ("observed", "too_large", "denied", "not_regular", "changed_during_read"):
                hash_state = "denied"
            if hash_state != "observed":
                state = "partial"
            rows.append({"id": "attachment:box/" + bot + "/" + path, "store": "box",
                         "agent_id": bot, "path": "attachments/" + bot + "/" + path,
                         "bytes": file["bytes"], "sha256": file["sha256"],
                         "hash_state": hash_state, "mtime": file["mtime"]})
    return rows, state
