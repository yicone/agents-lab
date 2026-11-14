"""Plugin cache declarations only; plugin skill bodies are never read."""
from .common import cache_entries, json_file, label, safe_id
from ..validation import InvalidDocument


def collect(ctx):
    data, state = json_file(ctx.roots.get("plugin_skills"), "cache.json", ctx.policy)
    if state != "observed":
        return [], state if state in ("absent", "denied", "not_collected") else "error"
    rows = []
    state = "ok"
    try:
        cache = cache_entries(data)
    except InvalidDocument:
        return [], "partial"
    for key, value in cache.items():
        if not safe_id(key):
            state = "partial"
            continue
        rows.append({"id": "grok-plugin:" + key, "name": label(value.get("name", key)),
                     "plugin": label(value.get("plugin"), 128), "version": label(value.get("version"), 64),
                     "evidence_level": "declared"})
    return rows, state
