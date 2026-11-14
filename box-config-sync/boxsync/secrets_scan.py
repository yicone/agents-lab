"""Secret findings contain rule, line and digest only; never matched text."""
import hashlib
import math
import re
from collections import Counter

from . import hashing  # loads the shared instructions module
from diff_instructions import SECRET_PATTERNS

# Specific rules from Board redact, plus its assignment rule. Its broad hex and
# base64 rules are applied separately with the design's explicit SHA exemption.
_PATTERNS = [("instructions_%d" % i, pattern) for i, pattern in enumerate(SECRET_PATTERNS)]
_PATTERNS += [
    ("multica_key", re.compile(r"\bm[a-z]{2}_[A-Za-z0-9_-]{16,}")),
    ("assignment", re.compile(r'(?i)\b(token|api[_-]?key|secret|password|passwd|authorization|cookie)("?\s*[=:]\s*"?)[^\s"\x27,;]+')),
    ("long_hex", re.compile(r"\b[A-Fa-f0-9]{40,}\b")),
    ("long_base64", re.compile(r"\b[A-Za-z0-9+/]{48,}={0,2}")),
]
_ENTROPY = re.compile(r"[A-Za-z0-9+/_=-]{32,}")
_HASH = re.compile(r"(?:[a-fA-F0-9]{40}|[a-fA-F0-9]{64})\Z")


def _entropy(value):
    size = len(value)
    return -sum((count / size) * math.log2(count / size) for count in Counter(value).values())


def scan(text):
    findings = []
    for line_number, line in enumerate(text.splitlines(), 1):
        for rule, pattern in _PATTERNS + [("high_entropy", _ENTROPY)]:
            for match in pattern.finditer(line):
                value = match.group()
                if rule in ("long_hex", "long_base64", "high_entropy") and _HASH.fullmatch(value):
                    continue
                if rule == "high_entropy" and _entropy(value) <= 4.5:
                    continue
                findings.append({"rule": rule, "line": line_number,
                                 "finding_id": hashlib.sha256(value.encode()).hexdigest()[:12]})
    return findings


def contains_secret(text):
    return bool(scan(text))
