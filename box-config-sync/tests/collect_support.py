"""Disposable complete synthetic roots; never changes the production registry."""
import json
from datetime import datetime, timezone
from pathlib import Path
from support import PROJECT
from boxsync.snapshot import Context

BOT = "00000000-0000-4000-8000-000000000001"
OTHER = "00000000-0000-4000-8000-000000000002"
WORKSPACE = "00000000-0000-4000-8000-000000000003"
NOW = datetime(2026, 11, 14, 2, 30, tzinfo=timezone.utc)
TOOL = {"commit": "3" * 40, "dirty": False, "version": "0.2.0"}
PROFILE_BODY = "Private profile prose never exported."
SKILL_BODY = "---\nname: Example\n---\nPrivate skill prose never exported.\n"
PROMPT = "Private routine prompt never exported."
TOKEN = "sk-" + "bait" * 8
CONFIG_VALUE = "private-config-value"


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) if not isinstance(value, str) else value, encoding="utf-8")


def fixture(tmp):
    base = Path(tmp) / "home/sand-data"
    roots = {name: base / leaf for name, leaf in
             {"profiles": "agents", "workflows": "workflows", "managed_skills": "managed-skills",
              "plugin_skills": "plugin-skills", "memory": "memory", "routines": "routines",
              "attachments": "attachments"}.items()}
    roots["mcps"] = None
    for root in roots.values():
        if root is not None:
            root.mkdir(parents=True)
    write(roots["profiles"] / BOT / "profile.json",
          {"name": "Example Bot", "description": PROFILE_BODY, "harness": "codex", "mcp_config": TOKEN})
    write(roots["profiles"] / OTHER / "profile.json", {"name": "Other Bot", "description": PROFILE_BODY})
    write(roots["workflows"] / "example/SKILL.md", SKILL_BODY)
    write(roots["workflows"] / "example/assets.txt", "Private auxiliary body.")
    write(roots["managed_skills"] / "skills/managed/SKILL.md", "Private managed body.")
    write(roots["managed_skills"] / "cache.json", {"skills": {"managed": {"name": "Managed", "version": "1.0"}}})
    write(roots["plugin_skills"] / "cache.json", {"skills": {"plugin-skill": {"name": "Plugin Skill", "plugin": "demo"}}})
    write(roots["memory"] / BOT / "memo.md", "Private memory body.")
    write(roots["memory"] / OTHER / "memo.md", "Other memory body.")
    write(roots["routines"] / BOT / "daily.json", {"id": "daily", "name": "Daily", "schedule": "0 1 * * *",
                                               "enabled": True, "prompt": PROMPT})
    write(roots["attachments"] / BOT / "image.bin", "Private attachment body.")
    settings = base / "settings.json"
    write(settings, {"notifyOnAgentUpdates": True,
                     "mcpBoxServers": [{"name": "demo", "env": {"DECOY": CONFIG_VALUE}, "url": "https://private.invalid"}]})
    for name in ("gateway.json", "teach-queue-key.json", ".env", "auth-token"):
        write(roots["workflows"] / "example" / name, TOKEN)
    return Context(roots=roots, settings=settings, offline=True, now=NOW), base.parent


def sidecar(bot=BOT):
    return {"schema": "box-config-sidecar/v1", "bot_id": bot, "captured_at": NOW.isoformat(),
            "mcp_servers": [{"name": "github", "status": "connected", "tool_count": 42, "origin": "plugin"}],
            "routines": [{"id": "daily", "name": "Daily", "enabled": True, "prompt_sha256": "a" * 64}]}
