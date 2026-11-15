"""Managed skills are read-only and checked against cache declarations."""
from ..hashing import body_sha256, tree_sha256
from .common import cache_entries, directory_names, json_file, label, safe_id
from ..validation import InvalidDocument


def collect(ctx):
    root = ctx.roots.get("managed_skills")
    if root is None:
        return [], "not_collected"
    names, state = directory_names(root / "skills", ctx.policy)
    data, cache_state = json_file(root, "cache.json", ctx.policy)
    try:
        cache = cache_entries(data)
    except InvalidDocument:
        cache, cache_state = {}, "error"
    rows = []
    if cache_state != "observed":
        state = "partial"
    for slug in sorted(set(names) | set(cache)):
        if not safe_id(slug):
            state = "partial"
            continue
        path = root / "skills" / slug
        body = body_sha256(path, "SKILL.md", ctx.policy)
        body.pop("body_bytes")
        tree = tree_sha256(path, ctx.policy)
        for obj, key, allowed in ((body, "body_hash_state", ("observed", "denied", "too_large", "missing", "not_regular", "changed_during_read")),
                                  (tree, "tree_state", ("observed", "denied", "too_large", "missing", "unsupported_entry", "changed_during_read"))):
            if obj[key] == "absent":
                obj[key] = "missing"
            elif obj[key] not in allowed:
                obj[key] = "denied"
        declared = cache.get(slug, {})
        status = ("cache_unreadable" if cache_state != "observed" else
                  "cache_mismatch" if slug not in names else
                  "not_in_cache" if slug not in cache else "listed")
        if status != "listed" or tree["tree_state"] != "observed" or body["body_hash_state"] != "observed":
            state = "partial"
        rows.append({"id": "grok-managed:" + slug, "slug": slug,
                     "name": label(declared.get("name")), "version": label(declared.get("version"), 64),
                     **body, **tree, "cache_state": status, "evidence_level": "observed"})
    return rows, state
