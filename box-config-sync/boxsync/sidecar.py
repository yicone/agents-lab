"""Untrusted per-bot input: strict schema, secret/URL rejection and freshness."""
import json
import re

from .domains.common import UUID
from .routine_config import routine_fingerprint
from .secrets_scan import contains_secret
from .validation import InvalidDocument, load_schema, parse_json, timestamp, validate

MAX_SIDECAR_BYTES = 256 * 1024
_SCHEMA = load_schema("sidecar")


def parse(data, now):
    bot = None
    try:
        if len(data) > MAX_SIDECAR_BYTES:
            raise InvalidDocument()
        value = parse_json(data)
        if isinstance(value, dict) and isinstance(value.get("bot_id"), str) and UUID.fullmatch(value["bot_id"]):
            bot = value["bot_id"]
        validate(value, _SCHEMA)
        if bot is None:
            raise InvalidDocument()
        text = json.dumps(value, ensure_ascii=False)
        if re.search(r"(?i)(?:[a-z][a-z0-9+.-]*://|www\.)", text):
            raise InvalidDocument()
        if contains_secret(text):
            return bot, None, "rejected_secret"
        age = abs((now - timestamp(value["captured_at"])).total_seconds())
        return bot, value, "stale_sidecar" if age > 900 else "ok"
    except (InvalidDocument, RecursionError):
        return bot, None, "rejected_schema"


def apply(ctx, domains, inputs):
    if not inputs:
        return "absent", []
    parsed = [parse(data, ctx.now) for _, data in inputs]
    seen = {}
    for bot, _, _ in parsed:
        if bot:
            seen[bot] = seen.get(bot, 0) + 1
    states, consumed = [], []
    for (source, _), (bot, value, state) in zip(inputs, parsed):
        if bot and seen[bot] > 1:
            value, state = None, "rejected_schema"
        states.append(state)
        if bot is None or value is None:
            continue
        evidence = "historical" if state == "stale_sidecar" else "observed"
        # Placeholder removal is limited to this Bot. Disk and Multica rows stay.
        if "mcps" in ctx.selected:
            domains["mcps"] = [r for r in domains["mcps"] if not (r.get("bot_id") == bot and r["kind"] == "bot_turn")]
            for server in value["mcp_servers"]:
                domains["mcps"].append({"id": "grok-mcp:" + bot + "/" + server["name"],
                                        "kind": "bot_turn", "bot_id": bot, "name": server["name"],
                                        "status": server["status"], "tool_count": server.get("tool_count"),
                                        "evidence_level": evidence})
        if "routines" in ctx.selected:
            domains["routines"] = [r for r in domains["routines"] if not (r["owner_id"] == bot and r["kind"] == "turn")]
            for routine in value["routines"]:
                safe = {k: routine[k] for k in ("name", "enabled", "schedule", "last_run_at", "next_run_at", "prompt_sha256") if k in routine}
                domains["routines"].append({"id": "grok-routine-live:" + bot + "/" + routine["id"],
                                            "kind": "turn", "owner_id": bot, "routine_id": routine["id"], **safe,
                                            "routine_sha256": routine_fingerprint(safe), "evidence_level": evidence})
        if {"mcps", "routines"}.issubset(ctx.selected):
            consumed.append(source)
    for state in ("rejected_secret", "rejected_schema", "stale_sidecar"):
        if state in states:
            return state, consumed
    return "ok", consumed
