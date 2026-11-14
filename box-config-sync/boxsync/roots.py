"""Only firsthand Box measurements may populate production roots.

This implementation ran on Darwin without a Box mount. The design's historical
paths are deliberately not promoted to measured production constants.
"""
from pathlib import Path

BOX_ROOTS = dict.fromkeys(("profiles", "workflows", "managed_skills", "plugin_skills",
                          "mcps", "memory", "routines", "attachments"))
SAND_DATA_PARENT = None
CONNECTOR_SECRETS_PARENT = None
LIBRARY_FINGERPRINT_FORMAT = None
LIBRARY_FINGERPRINT_KEY = None
LIBRARY_FINGERPRINT_HASH_SCOPE = None
BOX_PYTHON_VERSION = None
BOX_MULTICA_VERSION = None
NO_START_FLAG = None
ACCESSIBLE_WORKSPACES = None
BOX_MULTICA_IDENTITY = None
BOX_GITHUB_IDENTITY = None
SSH_Y_VERIFY = None
MINI_STATE_DIR = Path.home() / ".local/share/agents-lab/box-config-sync"
BOX_STATE_DIR = Path("/home/box/.local/state/box-config-sync")


def fixture_roots(home):
    """Explicit synthetic input; never fills or changes the production registry."""
    base = Path(home) / "agent-data"
    return {"profiles": base / "agents", "workflows": base / "workflows",
            "managed_skills": base / "managed-skills", "plugin_skills": base / "plugin-skills",
            "mcps": None, "memory": None, "routines": None, "attachments": None}
