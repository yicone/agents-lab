"""Stable directory identities, raw body and tree hashes; no body in output."""
import re
from collections import Counter
from ..hashing import body_sha256, tree_sha256
from ..policy import MAX_FILE_BYTES
from ..safe_io import read_file
from .common import directory_names, label, safe_id, walk_metadata


def frontmatter(data):
    try:
        lines = data.decode("utf-8").splitlines()
    except UnicodeError:
        return None, ["invalid_frontmatter"]
    if not lines or lines[0] != "---":
        return None, ["no_name"]
    try:
        end = lines.index("---", 1)
    except ValueError:
        return None, ["invalid_frontmatter"]
    names = [line[5:].strip() for line in lines[1:end] if line.startswith("name:")]
    if len(names) != 1:
        return None, ["invalid_frontmatter" if names else "no_name"]
    raw = names[0]
    if raw[:1] in ("|", ">", "{", "[", "&", "*", "!"):
        return None, ["invalid_frontmatter"]
    if raw[:1] in ("'", '"'):
        if len(raw) < 2 or raw[-1] != raw[0]:
            return None, ["invalid_frontmatter"]
        raw = raw[1:-1]
    name = label(raw)
    return name, [] if name else ["invalid_frontmatter"]


def collect(ctx):
    root = ctx.roots.get("workflows")
    names, state = directory_names(root, ctx.policy)
    rows = []
    for slug in names:
        if not safe_id(slug):
            state = "partial"
            continue
        path = root / slug
        body = body_sha256(path, "SKILL.md", ctx.policy)
        tree = tree_sha256(path, ctx.policy)
        if tree["tree_state"] == "absent":
            tree["tree_state"] = "missing"
        if tree["tree_state"] not in ("observed", "missing", "denied", "too_large", "unsupported_entry", "changed_during_read"):
            tree["tree_state"] = "denied"
        body.pop("body_bytes")
        if body["body_hash_state"] not in ("observed", "missing", "denied", "too_large", "not_regular", "changed_during_read"):
            body["body_hash_state"] = "denied"
        content = read_file(path, "SKILL.md", ctx.policy)
        name, flags = frontmatter(content.data) if content.state == "observed" else (None, ["no_name"])
        files, file_state = walk_metadata(path, ctx.policy, content=True, max_bytes=MAX_FILE_BYTES)
        # Never export potentially secret names, including otherwise valid paths.
        safe_files = [{"path": f["path"], "bytes": f["bytes"], "sha256": f["sha256"],
                       "state": f["state"] if f["state"] in ("observed", "too_large") else "error"}
                      for f in files if label(f["path"], 1024) is not None]
        if len(safe_files) != len(files):
            state = "partial"
        if content.state != "observed" or tree["tree_state"] != "observed" or file_state != "ok":
            state = "partial"
        rows.append({"id": "grok-workflow:" + slug, "dir_name": slug,
                     "kind": "numeric" if re.fullmatch(r"skill-\d{10,}", slug) else "named",
                     "name": name, "name_key": name.casefold() if name else None, **body, **tree,
                     "files": safe_files, "files_truncated": file_state != "ok",
                     "file_count": len(files), "total_bytes": sum(f["bytes"] or 0 for f in files),
                     "fingerprint_state": "format_unconfirmed", "flags": flags,
                     "evidence_level": "observed" if tree["tree_state"] == "observed" else "unknown"})
    duplicates = Counter(row["name_key"] for row in rows if row["name_key"])
    for row in rows:
        if duplicates[row["name_key"]] > 1:
            row["flags"].append("duplicate_name")
    return rows, state
