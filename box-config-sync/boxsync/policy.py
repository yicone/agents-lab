"""Name-only hard exclusions. Never inspect an excluded directory entry."""
from collections import Counter
from fnmatch import fnmatchcase
from pathlib import PurePath

BLACKLIST_VERSION = "2026-11-07"
MAX_FILE_BYTES = 512 * 1024
MAX_CONFIG_BYTES = 1024 * 1024
MAX_ATTACHMENT_BYTES = 64 * 1024 * 1024
MAX_TREE_ENTRIES = 2000

_GROUPS = (
    ("credential_directory", ("connector-secrets", "box-secrets", "host-secrets", "*-secrets",
                              "secrets", ".ssh", ".gnupg", ".aws")),
    ("environment_key", (".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "*.keystore",
                         "id_rsa*", "id_ed25519*", ".netrc", ".git-credentials", ".npmrc", ".pypirc")),
    ("sensitive_name", ("*token*", "*secret*", "*credential*", "*password*", "cookies*", "*keychain*")),
    ("database_session", ("*.sqlite*", "*.db", "*.db-wal", "*.db-shm", "*transcript*", "*composer-draft*")),
)


class Policy:
    def __init__(self, extra_patterns=()):
        self.extra_patterns = tuple(pattern.lower() for pattern in extra_patterns)
        self.skips = Counter()

    def category(self, name, parent=""):
        name = name.lower()
        if name == "gh" and PurePath(parent).name.lower() == ".config":
            return "credential_directory"
        for category, patterns in _GROUPS:
            if any(fnmatchcase(name, pattern) for pattern in patterns):
                return category
        if any(fnmatchcase(name, pattern) for pattern in self.extra_patterns):
            return "additional"
        return None

    def skip(self, name, parent=""):
        category = self.category(name, parent)
        if category:
            self.skips[category] += 1
        return category is not None

    def denied_path(self, path):
        parts = PurePath(path).parts
        return any(part == ".." or self.category(part, parts[i - 1] if i else "")
                   for i, part in enumerate(parts))
