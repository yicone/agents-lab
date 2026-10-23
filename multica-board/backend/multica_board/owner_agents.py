"""User-run, read-only agent inventory. Never invoke collection from the server."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile

from .collector import iso, utcnow, norm_agent

PROFILE = "desktop-api.multica.ai"
SOURCE = "user-cli:desktop-api.multica.ai"
WORKSPACES = {
    "b422f594-1fbc-42e6-8da0-bb98c5ea92c5": "podcast-foundry",
    "b648cd0b-31bf-40fd-8bc1-3f60987d2532": "news-for-me",
    "a099e534-e5ed-4008-9fc0-d8c3ea1d0326": "posts-rebuild",
    "ffa95ec6-4e32-4a09-bd10-c707230ae40d": "sprout-coach",
    "bbf46bdb-418e-4c5c-a0d4-465cfe33cfa3": "elder-oasis",
    "e6fcf378-7d4a-43de-b196-a25c0b6a4aa1": "english-study",
    "66ddc1bb-08c3-44de-b4c4-076793dae782": "clipvibe",
    "a969cf95-3650-4369-93f1-9fa6cd280249": "aimaiai",
    "a3edff75-e74d-46f8-88d5-fcb8054b4059": "yicone-brand",
}


def snapshot_path(db: Path) -> Path:
    return db.with_name(db.stem + "-owner-agents.json")


def load_snapshot(path: Path, profile: str) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if (data.get("version") != 1 or data.get("profile") != profile
                or data.get("source") != SOURCE or not isinstance(data.get("workspaces"), dict)):
            return {}
        return data
    except (OSError, ValueError, AttributeError):
        return {}


def collect(path: Path, *, execute=subprocess.run) -> dict:
    """Only the user calls this; injected execution permits credential-free tests."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("MULTICA_")}
    env["GODEBUG"] = "tlsmlkem=0"
    data = {"version": 1, "profile": PROFILE, "source": SOURCE,
            "collected_at": iso(utcnow()), "workspaces": {}}
    # No old workspace data survives a failed attempt: stale instructions must not look current.
    with tempfile.TemporaryDirectory(prefix="atlas-owner-cli-") as cwd:
        for ws, slug in WORKSPACES.items():
            result = {"slug": slug, "collected_at": iso(utcnow()), "agents": []}
            try:
                proc = execute(
                    ["multica", "--profile", PROFILE, "--workspace-id", ws,
                     "agent", "list", "--include-archived", "--output", "json"],
                    env=env, cwd=cwd, capture_output=True, text=True,
                    stdin=subprocess.DEVNULL, timeout=60,
                )
                if proc.returncode:
                    raise RuntimeError("cli_failed")
                raw = json.loads(proc.stdout)
                agents = raw.get("agents") if isinstance(raw, dict) else raw
                if not isinstance(agents, list):
                    raise ValueError("invalid inventory")
                for agent in agents:
                    if (not isinstance(agent, dict) or not isinstance(agent.get("id"), str)
                            or not agent["id"] or not isinstance(agent.get("name"), str)
                            or not agent["name"]):
                        raise ValueError("invalid agent")
                    if agent.get("workspace_id", ws) != ws:
                        raise ValueError("workspace mismatch")
                result.update(status="ok", agents=[norm_agent(a) for a in agents])
            except subprocess.TimeoutExpired:
                result.update(status="error", error="timeout")
            except OSError:
                result.update(status="error", error="cli_unavailable")
            except (RuntimeError, ValueError, TypeError):
                # Never persist or print raw CLI output, exception messages or credentials.
                result.update(status="error", error="cli_failed_or_invalid_inventory")
            data["workspaces"][ws] = result
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".owner-agents-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, path)  # private 0600 file; readers see a complete generation
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return data


def overlay(rows: list[dict], path: Path, profile: str) -> tuple[list[dict], dict]:
    data = load_snapshot(path, profile)
    keyed = {(r["workspace_id"], r["id"]): r for r in rows}
    report = {}
    for ws, entry in data.get("workspaces", {}).items():
        if ws not in WORKSPACES or not isinstance(entry, dict):
            continue
        report[ws] = {k: entry.get(k) for k in ("status", "error", "collected_at", "slug")}
        if entry.get("status") != "ok" or not isinstance(entry.get("agents"), list):
            continue
        # Successful inventory replaces this workspace, including deletions and an empty list.
        keyed = {k: v for k, v in keyed.items() if k[0] != ws}
        for row in entry["agents"]:
            if not isinstance(row, dict) or not row.get("id"):
                continue
            keyed[(ws, row["id"])] = {**row, "workspace_id": ws,
                "inventory_source": SOURCE, "inventory_collected_at": entry.get("collected_at")}
    return sorted(keyed.values(), key=lambda r: r.get("name") or ""), report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path.home() / ".multica-board/board.sqlite",
                        help="Atlas DB location (only its sibling JSON file is written)")
    args = parser.parse_args()
    data = collect(snapshot_path(args.db.expanduser()))
    for entry in data["workspaces"].values():
        print(f"{entry['slug']}: {entry['status']} ({len(entry['agents'])} agents)")
    print(f"collected_at: {data['collected_at']}; source: {SOURCE}")
    return 0 if all(e["status"] == "ok" for e in data["workspaces"].values()) else 2
