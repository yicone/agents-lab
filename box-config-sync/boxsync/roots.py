"""Box roots and capabilities measured by Atlas PM on 2026-11-14.

Evidence: ACM-925 attachment acm925-box-s2-results.md, comment
01a1d79b-304f-7dba-88e3-e5451a6b111f. This is supplied Linux evidence,
not a measurement made by this implementation's Darwin test runner.
"""
from pathlib import Path

BOX_HOST = "grok-bot-vm-359964430"
MEASURED_AT = "2026-11-14T09:08:00+08:00"
SAND_DATA_PARENT = Path("/home/box/sand-data")
CONNECTOR_SECRETS_PARENT = SAND_DATA_PARENT
# Historical agent-data paths are symlink aliases, never traversal roots.
AGENT_DATA_ALIAS = Path("/home/box/agent-data")
SETTINGS_PATH = SAND_DATA_PARENT / "settings.json"
BOX_ROOTS = {
    "profiles": SAND_DATA_PARENT / "agents",
    "workflows": SAND_DATA_PARENT / "workflows",
    "managed_skills": SAND_DATA_PARENT / "managed-skills",
    "plugin_skills": SAND_DATA_PARENT / "plugin-skills",
    # The attachment does not verify these subdirectories or turn-only sources.
    "mcps": None,
    "memory": None,
    "routines": None,
    "attachments": None,
}
LIBRARY_FINGERPRINT_PATH = BOX_ROOTS["workflows"] / ".library-fingerprint"
LIBRARY_FINGERPRINT_FORMAT = "json"
LIBRARY_FINGERPRINT_ENCODING = "utf-8"
LIBRARY_FINGERPRINT_BOM = False
LIBRARY_FINGERPRINT_NEWLINE = False
LIBRARY_FINGERPRINT_KEY = "skill_slug"
LIBRARY_FINGERPRINT_VALUE_PATTERN = r"[0-9a-f]{64}"
# Digest shape alone cannot establish SKILL.md versus tree hashing semantics.
LIBRARY_FINGERPRINT_HASH_SCOPE = None
BOX_PYTHON_VERSION = "3.13.5"
BOX_PYTHON_EXECUTABLE = Path("/usr/bin/python3")
BOX_MULTICA_VERSION = "0.8.0"
BOX_MULTICA_COMMIT = "9b1ab4f9e"
BOX_MULTICA_BUILD = "2026-11-03T10:26:43Z"
BOX_MULTICA_PLATFORM = "linux/amd64"
BOX_MULTICA_EXECUTABLE = Path("/home/box/.local/bin/multica")
BOX_MULTICA_PROFILE = "atlas-pm"
# Audit vocabulary for later writers; unsupported flags must never be passed.
NO_START_FLAG = "unsupported"
ACCESSIBLE_WORKSPACES = ("8ebc87ea-27f2-42c4-90f6-24a04e64d3f5",)
BOX_MULTICA_IDENTITY = "yadhiel+atlas-pm@mail.grokbot.com"
BOX_GITHUB_IDENTITY = "yicone"
SSH_KEYGEN_EXECUTABLE = Path("/usr/bin/ssh-keygen")
SSH_VERSION = "OpenSSH_10.0p2 Debian-7+deb13u4"
SSH_Y_VERIFY = True
MINI_STATE_DIR = Path.home() / ".local/share/agents-lab/box-config-sync"
BOX_STATE_DIR = Path("/home/box/.local/state/box-config-sync")


def fixture_roots(home):
    """Explicit synthetic input; never fills or changes the production registry."""
    base = Path(home) / "sand-data"
    return {"profiles": base / "agents", "workflows": base / "workflows",
            "managed_skills": base / "managed-skills", "plugin_skills": base / "plugin-skills",
            "mcps": None, "memory": None, "routines": None, "attachments": None}
