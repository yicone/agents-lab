#!/usr/bin/env python3
"""Read-only, one-shot Git / deployed Board / observed instructions matrix."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import sys
from urllib.request import urlopen

import diff_instructions as source


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def digest(value):
    return value if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) else None


def state(git, board, live):
    if not all((git, board, live)):
        return "stale"
    if git == board == live:
        return "aligned"
    if git == live:
        return "stale"  # deployed Board has an old export
    if len({git, board, live}) == 3:
        return "split"
    return "modified"


def matrix(root, board, agents, bots, *, observed_at, freshness=None):
    """No bodies in the result. Identity is (kind, workspace_id, id)."""
    freshness = freshness or {}
    rows = []
    for kind, git_rows, live_rows in (
        ("agent", source.git_agents(root), agents),
        ("bot", source.git_bots(root), bots),
    ):
        def key(row):
            return (row.get("workspace_id") if kind == "agent" else None, row["id"])

        git_by = {key(row): row for row in git_rows}
        live_by = {key(row): row for row in live_rows}
        board_by = {}
        for identity, row in board.get(kind + "s", {}).items():
            if not isinstance(row, dict):
                continue
            if kind == "agent":
                ws, sep, rid = identity.partition("/")
                if not sep:
                    continue
                board_by[(ws, rid)] = row
            else:
                board_by[(None, identity)] = row
        for identity in sorted(git_by.keys() | live_by.keys() | board_by.keys(), key=str):
            git = git_by.get(identity, {})
            deployed = board_by.get(identity, {})
            live = live_by.get(identity, {})
            field = "instructions" if kind == "agent" else "description"
            gh = digest(git.get("sha256"))
            bh = digest(deployed.get("sha256"))
            lh = digest(source.live_hash(live, field + "_sha256", field))
            reasons = []
            withheld = bool(git.get("withheld") or deployed.get("withheld"))
            if deployed and not isinstance(deployed.get("text"), str):
                reasons.append("board_body_missing")
            if "text" in deployed and source.instructions_sha256(deployed["text"]) != bh:
                reasons.append("board_hash_mismatch")
            for name, hash_value in (("git", gh), ("board", bh), ("live", lh)):
                if hash_value is None:
                    reasons.append(name + "_hash_missing")
            if freshness.get("board", "fresh") != "fresh":
                reasons.append("board_stale")
            if freshness.get(kind) != "fresh":
                reasons.append("live_" + freshness.get(kind, "unknown"))
            comparison = state(gh, bh, lh)
            if gh and gh == lh and bh and gh != bh:
                reasons.append("board_outdated")
            drift = "withheld" if withheld else "stale" if reasons else comparison
            rows.append({
                "kind": kind, "workspace_id": identity[0], "id": identity[1],
                "path": "instructions-governance/sources/" + git["path"] if git.get("path") else None,
                "git_sha256": None if withheld else gh,
                "board_sha256": None if withheld else bh,
                "live_sha256": None if withheld else lh,
                "updated_at": observed_at.get(kind), "drift": drift,
                "comparison": comparison if not withheld else "withheld",
                "stale_reasons": reasons,
            })
    return {"schema": "instruction-query/v1", "items": rows,
            "summary": dict(Counter(row["drift"] for row in rows))}


def snapshot(path, kind):
    payload = source._read_json(path)
    rows = source._items(payload, kind)
    identities = set()
    for row in rows:
        if not isinstance(row.get("id"), str) or not row["id"]:
            raise ValueError("invalid identity")
        if kind == "agents" and (not isinstance(row.get("workspace_id"), str) or not row["workspace_id"]):
            raise ValueError("missing workspace")
        identity = (row.get("workspace_id"), row["id"])
        if identity in identities:
            raise ValueError("duplicate identity")
        identities.add(identity)
    # File mtime is observation evidence, never the object's last edit time.
    return rows, datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=source.ROOT)
    board_input = parser.add_mutually_exclusive_group()
    board_input.add_argument("--board-json", type=Path, help="offline Board export (not the Git index)")
    board_input.add_argument("--board-url", default="http://127.0.0.1:8765/instruction-sources.json")
    parser.add_argument("--agents-json", type=Path)
    parser.add_argument("--bots-json", type=Path)
    parser.add_argument("--multica", default="multica")
    parser.add_argument("--roster-dir", type=Path, default=source.default_roster_dir())
    parser.add_argument("--max-age-seconds", type=int, default=300)
    args = parser.parse_args(argv)
    if args.max_age_seconds < 0:
        parser.error("--max-age-seconds must be nonnegative")
    now = datetime.now(timezone.utc)
    errors = {}
    observed = {}
    freshness = {}
    origins = {}
    try:
        if args.board_json:
            board = source._read_json(args.board_json)
            board_stamp = datetime.fromtimestamp(args.board_json.stat().st_mtime, timezone.utc)
            age = (now - board_stamp).total_seconds()
            freshness["board"] = "fresh" if 0 <= age <= args.max_age_seconds else "stale"
        else:
            with urlopen(args.board_url, timeout=10) as response:
                board = json.load(response)
        if (not isinstance(board, dict) or board.get("schema") != "instruction-sources/v1"
                or any(not isinstance(board.get(k), dict) for k in ("agents", "bots"))):
            raise ValueError("invalid Board schema")
    except (OSError, ValueError):
        board = {}
        errors["board"] = "unavailable_or_invalid"
    inaccessible = []
    values = {}
    for kind, path in (("agent", args.agents_json), ("bot", args.bots_json)):
        stamp = None
        origins[kind] = "snapshot" if path else "multica-cli" if kind == "agent" else "local-roster-cache"
        try:
            if path:
                values[kind], stamp = snapshot(path, kind + "s")
            elif kind == "agent":
                values[kind], inaccessible = source.collect_agents(source.multica_runner(args.multica, timeout=30))
                stamp = datetime.now(timezone.utc)
            else:
                stamps = []

                def reader(p):
                    if source.slice_kind(source.decode_blob_name(p.name) or "") == "roster":
                        stamps.append(datetime.fromtimestamp(p.stat().st_mtime, timezone.utc))
                    return p.read_text(encoding="utf-8")

                values[kind] = source.read_roster_descriptions(args.roster_dir, reader=reader)
                stamp = min(stamps) if stamps else None
        except (OSError, ValueError, SystemExit, TypeError, KeyError, subprocess.TimeoutExpired):
            values[kind] = []
            errors[kind] = "unavailable_or_invalid"
        observed[kind] = iso(stamp) if stamp else None
        age = (datetime.now(timezone.utc) - stamp).total_seconds() if stamp else None
        freshness[kind] = "fresh" if age is not None and 0 <= age <= args.max_age_seconds else "stale"
    report = matrix(args.root, board, values["agent"], values["bot"],
                    observed_at=observed, freshness=freshness)
    report.update(generated_at=iso(now), live_sources=origins, freshness=freshness,
                  board_source="snapshot" if args.board_json else "http",
                  source_errors=errors,
                  inaccessible_workspaces=[{"workspace_id": w.get("workspace_id")} for w in inaccessible])
    json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
