"""Redact secrets before a reason is stored or matched into evidence."""

from __future__ import annotations

import re

REDACTED = "[redacted]"

_SECRET_PATTERNS = [
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"\bm[a-z]{2}_[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{8,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    re.compile(
        r"(?i)\b(token|api[_-]?key|secret|password|passwd|authorization|cookie)"
        r"(\"?\s*[=:]\s*\"?)[^\s\"',;]+"
    ),
    re.compile(r"\b[A-Fa-f0-9]{40,}\b"),
    re.compile(r"\b[A-Za-z0-9+/]{48,}={0,2}"),
]
_HOME_PATH = re.compile(r"/(?:Users|home)/[^/\s\"']+")


def redact(text: str | None, limit: int | None = 200) -> str | None:
    if text is None:
        return None
    out = str(text)
    for pattern in _SECRET_PATTERNS:
        if pattern.groups >= 2:
            out = pattern.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", out)
        else:
            out = pattern.sub(REDACTED, out)
    out = _HOME_PATH.sub("~", out)
    if limit is not None and len(out) > limit:
        out = out[: limit - 1] + "…"
    return out
