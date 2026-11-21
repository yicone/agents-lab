"""Data-branch allowlist: only ever read from the pinned <remote>/main commit.

Both publish (producer) and fetch (consumer) load the same document via
``GitReader.file``; working-tree and data-branch copies are never consulted.
Validation is strict and fails closed (``AllowlistError`` -> exit 4,
``allowlist_unavailable``).

Only ``box-config-snapshot/v1`` content is supported: the snapshot validator
is hard-wired to ``SNAPSHOT_SCHEMA_FILE``, so an entry's ``schema_file`` is
checked for equality with that path and never used to load an arbitrary
schema (the design's ``multica-board/docs/schemas/...`` path does not exist;
the repo path is used).
"""
import re

from .validation import InvalidDocument, parse_json

ALLOWLIST_PATH = "box-config-sync/data-branch-allowlist.json"
ALLOWLIST_SCHEMA = "data-branch-allowlist/v1"
DATA_BRANCH = "box-snapshots"
SNAPSHOT_SCHEMA_FILE = "box-config-sync/schemas/box-config-snapshot.schema.json"
MAX_REGEX_BYTES = 512
MAX_MAX_BYTES = 4 * 1024 * 1024

_ENTRY_KEYS = {"id", "path_pattern", "path_regex", "content_schema", "schema_file",
               "max_bytes", "retention", "producer", "consumers", "approved_by"}
_RETENTION_KEYS = {"tree", "history_days", "rewrite_history"}


class AllowlistError(Exception):
    pass


class Allowlist:
    def __init__(self, branch, entries):
        self.branch = branch
        self.entries = tuple(entries)

    def matches(self, path):
        return any(entry["regex"].fullmatch(path) for entry in self.entries)

    def entry_for(self, content_schema, path):
        for entry in self.entries:
            if entry["content_schema"] == content_schema and entry["regex"].fullmatch(path):
                return entry
        return None


def _entry(value):
    if not isinstance(value, dict) or set(value) != _ENTRY_KEYS:
        raise AllowlistError()
    if (not isinstance(value["id"], str) or not value["id"]
            or not isinstance(value["path_pattern"], str)
            or not isinstance(value["schema_file"], str)
            or not isinstance(value["producer"], str)
            or not isinstance(value["approved_by"], str)
            or not isinstance(value["consumers"], list)
            or any(not isinstance(consumer, str) for consumer in value["consumers"])):
        raise AllowlistError()
    regex = value["path_regex"]
    if not isinstance(regex, str) or not regex or len(regex.encode("utf-8")) > MAX_REGEX_BYTES:
        raise AllowlistError()
    try:
        compiled = re.compile(regex)
    except re.error:
        raise AllowlistError() from None
    # Only snapshot content is supported so far; anything else is refused.
    if value["content_schema"] != "box-config-snapshot/v1":
        raise AllowlistError()
    if value["schema_file"] != SNAPSHOT_SCHEMA_FILE:
        raise AllowlistError()
    max_bytes = value["max_bytes"]
    if type(max_bytes) is not int or not 1 <= max_bytes <= MAX_MAX_BYTES:
        raise AllowlistError()
    retention = value["retention"]
    if (not isinstance(retention, dict) or set(retention) != _RETENTION_KEYS
            or retention["tree"] != "latest_only"
            or type(retention["history_days"]) is not int or retention["history_days"] < 1
            or retention["rewrite_history"] is not False):
        raise AllowlistError()
    return {"id": value["id"], "regex": compiled, "path_regex": regex,
            "content_schema": value["content_schema"], "max_bytes": max_bytes,
            "approved_by": value["approved_by"], "history_days": retention["history_days"]}


def validate_allowlist(doc):
    if not isinstance(doc, dict) or doc.get("schema") != ALLOWLIST_SCHEMA:
        raise AllowlistError()
    if doc.get("branch") != DATA_BRANCH:
        raise AllowlistError()
    if (not isinstance(doc.get("excluded_kinds"), list)
            or any(not isinstance(kind, str) for kind in doc["excluded_kinds"])):
        raise AllowlistError()
    entries = doc.get("entries")
    if not isinstance(entries, list) or not entries:
        raise AllowlistError()
    seen = set()
    result = []
    for value in entries:
        entry = _entry(value)
        if entry["id"] in seen:
            raise AllowlistError()
        seen.add(entry["id"])
        result.append(entry)
    return Allowlist(DATA_BRANCH, result)


def load_allowlist(reader):
    """Read and validate the allowlist at the reader's pinned <remote>/main."""
    data = reader.file(ALLOWLIST_PATH)  # GitError propagates as unreadable
    if data is None:
        raise AllowlistError()
    try:
        doc = parse_json(data)
    except InvalidDocument:
        raise AllowlistError() from None
    return validate_allowlist(doc)
