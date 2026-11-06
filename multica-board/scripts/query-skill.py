#!/usr/bin/env python3
"""Query Board skill registration and declared consumers without reading bodies."""
from __future__ import annotations

import argparse
import http.client
import json
from pathlib import Path
import sys
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener

URL = "http://127.0.0.1:8765/api/skills"
MAX_BYTES = 16 * 1024 * 1024
ROW_FIELDS = ("id", "name", "canonical_path", "body_sha256", "body_hash_path",
              "body_hash_state", "source_class", "reconciliation_status")
LOADER_FIELDS = ("id", "kind", "mark", "shared_auto_load", "runtime_loaded",
                 "load_state", "source", "collected_at", "workspace_id", "skill_id", "enabled")
ENTRY_FIELDS = ("path", "content_hash", "hash_file", "hash_state")
COMPARE_FIELDS = ("algorithm", "subject", "status", "entry_count",
                  "compared_entry_count", "unknown_entry_indexes")
REQUIRED_FIELDS = ("id", "name", "canonical_path", "body_sha256", "body_hash_state",
                   "source_class", "loaders")


class QueryError(ValueError):
    """Fixed, body-free diagnostic safe to print."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def pick(row, fields):
    return {key: row.get(key) for key in fields}


def read_document(fixture=None):
    # No proxy, redirects, retries, fallback, or content endpoint. This command
    # only reads the loopback Board or an explicitly selected offline fixture.
    if fixture is not None:
        with fixture.open("rb") as handle:
            raw = handle.read(MAX_BYTES + 1)
    else:
        with build_opener(ProxyHandler({}), NoRedirect()).open(URL, timeout=30) as response:
            if response.geturl() != URL:
                raise QueryError("unexpected Board redirect")
            raw = response.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise QueryError("skills response exceeds 16 MiB")
    document = json.loads(raw)
    if not isinstance(document, dict) or document.get("ok") is not True:
        raise QueryError("Board skills payload is unavailable (ok != true)")
    items = document.get("items")
    if not isinstance(items, list):
        raise QueryError("invalid Board items")
    for row in items:
        if (not isinstance(row, dict) or not isinstance(row.get("id"), str)
                or not isinstance(row.get("name"), str)
                or not set(REQUIRED_FIELDS) <= row.keys()
                or not isinstance(row["loaders"], list)
                or any(not isinstance(loader, dict) for loader in row["loaders"])):
            raise QueryError("invalid or legacy registration; current ACM-825 fields required")
    return document


def project(row):
    """Allowlisted metadata only; nested content from future payloads stays out."""
    result = pick(row, ROW_FIELDS)
    result["loaders"] = [pick(loader, LOADER_FIELDS) for loader in row["loaders"]]
    # Names come from declaration lists; workspace identity must stay attached.
    result["declarations"] = [
        {"kind": "workspace_agent", "workspace_id": install.get("workspace_id"),
         "workspace_name": install.get("workspace_name"), "skill_id": install.get("id"),
         **pick(agent, ("id", "name", "enabled")), "load_state": "unverified"}
        for install in row.get("workspace_installs") or []
        for agent in install.get("agents") or []
    ] + [
        {"kind": "grok_bot", **pick(bot, ("id", "name", "evidence_state")),
         "load_state": "unverified"}
        for bot in row.get("grok_bots") or []
    ]
    result["entries"] = [pick(entry, ENTRY_FIELDS) for entry in row.get("entries") or []]
    comparison = row.get("hash_compare")
    result["hash_compare"] = None
    result["hash_compare_state"] = "unavailable"
    if isinstance(comparison, dict):
        result["hash_compare"] = {
            **pick(comparison, COMPARE_FIELDS),
            "groups": [pick(group, ("content_hash", "entry_indexes"))
                       for group in comparison.get("groups") or []],
        }
        result["hash_compare_state"] = "available"
    return result


def query(document, *, name=None, skill_id=None):
    rows = [project(row) for row in document["items"]
            if (row["id"] == skill_id if skill_id is not None else row["name"] == name)]
    if not rows:
        raise QueryError("skill not found (exact name/id match required)")
    return {"ok": True, "generated_at": document.get("generated_at"), "items": rows}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, epilog="""
Steward / execution agents: use --name colgrep-search --json first; a name may
return separate local and Grok rows. Pin --id from that response for later reads.
Use loaders plus declarations to find workspace_agent / grok_bot consumers;
enabled/shared presence does not prove runtime loading. entries indexes align
with hash_compare.groups; comparison is independent of baseline drift.
--fixture is explicit offline evidence, never a live Board observation.
No SKILL content is emitted or fetched. No inventory writes or activation.
Exit codes: 0 matched, 1 unavailable/invalid/not found, 2 invalid arguments.
""")
    selector = parser.add_mutually_exclusive_group(required=True)
    selector.add_argument("--name", help="exact skill name; keeps all source-specific rows")
    selector.add_argument("--id", dest="skill_id", help="exact Board skill id")
    parser.add_argument("--json", action="store_true", help="machine-readable JSON")
    parser.add_argument("--fixture", type=Path, help="offline GET /api/skills JSON snapshot")
    args = parser.parse_args(argv)
    try:
        result = query(read_document(args.fixture), name=args.name, skill_id=args.skill_id)
        result["input"] = "fixture" if args.fixture else "board"
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print("input: " + result["input"])
            print("generated_at: " + str(result["generated_at"]))
            for row in result["items"]:
                print()
                for key, value in row.items():
                    print(f"{key}: {json.dumps(value, ensure_ascii=False)}")
        return 0
    except (OSError, ValueError, TypeError, KeyError, AttributeError, http.client.HTTPException) as exc:
        # Do not echo payloads, URLs, raw service errors, or local file bodies.
        reason = str(exc) if isinstance(exc, QueryError) else type(exc).__name__
        if args.json:
            print(json.dumps({"ok": False, "error": "skill_query_failed",
                              "reason": reason}), file=sys.stderr)
        else:
            print(f"skill_query_failed ({reason}); check Board availability, "
                  "current registration schema, fixture and exact selector", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
