"""Secret redaction and run-error classification."""

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

MAX_ERROR_CHARS = 200


def redact(text: str | None, limit: int | None = MAX_ERROR_CHARS) -> str | None:
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


ERROR_KINDS = (
    "quota",
    "rate_limit",
    "auth",
    "model_unavailable",
    "daemon_restart",
    "timeout",
    "network",
    "cancelled",
    "process",
    "unknown",
)

_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("quota", re.compile(r"(?i)usage limit|quota|insufficient[_ ]?(credit|balance|funds)|billing|credit balance|exceeded your current")),
    ("rate_limit", re.compile(r"(?i)\b429\b|rate[ _-]?limit|too many requests|overloaded")),
    ("auth", re.compile(r"(?i)\b(401|403)\b|unauthori[sz]ed|unauthenticated|forbidden|authentication|not logged in|invalid (api )?key|permission denied|not a member")),
    ("model_unavailable", re.compile(r"(?i)model[^\n]{0,40}(not found|unavailable|not supported|does not exist|unknown)|unknown model|no such model")),
    ("daemon_restart", re.compile(r"(?i)daemon restart(ed)?|daemon (was )?(stopped|shut ?down|exited)")),
    ("timeout", re.compile(r"(?i)timed? ?out|deadline exceeded|timeout")),
    ("network", re.compile(r"(?i)connection (refused|reset|closed)|econn|errno 6[01]|network|dns|unreachable|tls|eof|\b50[234]\b")),
    ("cancelled", re.compile(r"(?i)cancel")),
    ("process", re.compile(r"(?i)process exited|exit(ed)? (code|status)|initiali[sz]e failed|crash|panic|killed")),
]


def classify_error(text: str | None) -> str | None:
    """Map a raw error to the contract's error_kind. First matching rule wins (see ERROR_KINDS order)."""
    if not text:
        return None
    for name, pattern in _RULES:
        if pattern.search(text):
            return name
    return "unknown"
