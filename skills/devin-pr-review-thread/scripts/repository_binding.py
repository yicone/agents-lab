#!/usr/bin/env python3
"""Pure helpers for repository-level Devin session binding.

This module deliberately performs no registry or Devin mutations.  Callers
provide the already-resolved Git roots and origin remotes they observed.
"""
from __future__ import annotations

import json
import pathlib
import re
from typing import Any
from urllib.parse import urlparse


class BindingError(ValueError):
    """A repository binding is invalid or ambiguous."""


def normalize_origin(remote: str) -> str:
    """Return canonical ``owner/repository`` for a GitHub SSH/HTTPS remote."""
    value = remote.strip()
    if not value or "\n" in value or "\r" in value:
        raise BindingError("origin is empty or contains newlines")
    if "://" not in value and not value.startswith("git@") and value.count("/") == 1:
        path = value
        parts = path.split("/")
        if all(re.fullmatch(r"[A-Za-z0-9_.-]+", part or "") for part in parts):
            return path
    if "@" in value and not value.startswith("git@github.com:"):
        # Reject URL credentials and non-GitHub userinfo forms.
        if "://" in value or not value.startswith("git@github.com:"):
            raise BindingError("origin credentials or unsupported host")
    if value.startswith("git@"):
        if not value.startswith("git@github.com:"):
            raise BindingError("origin host is not github.com")
        path = value.split(":", 1)[1]
        if any(ch in path for ch in "?#"):
            raise BindingError("origin query or fragment is not allowed")
    else:
        parsed = urlparse(value)
        if parsed.scheme not in {"https", "http"} or parsed.hostname != "github.com":
            raise BindingError("origin must be a GitHub HTTPS URL")
        if parsed.username or parsed.password or parsed.port:
            raise BindingError("origin credentials or port are not allowed")
        if parsed.query or parsed.fragment:
            raise BindingError("origin query or fragment is not allowed")
        path = parsed.path.lstrip("/")
    if path.endswith(".git"):
        path = path[:-4]
    parts = path.split("/")
    if len(parts) != 2 or any(not part or part in {".", ".."} for part in parts):
        raise BindingError("origin must contain exactly owner/repository")
    if any(not re.fullmatch(r"[A-Za-z0-9_.-]+", part) for part in parts):
        raise BindingError("origin contains unsupported characters")
    return "/".join(parts)


def load_origin_entry(registry: dict[str, Any], origin: str) -> dict[str, Any]:
    """Resolve one v2 origin entry and reject duplicate/contradictory bindings."""
    normalized = normalize_origin(origin)
    if not isinstance(registry, dict):
        raise BindingError("registry must be an object")
    matches: list[tuple[str, dict[str, Any]]] = []
    session_ids: dict[str, str] = {}
    for key, raw in registry.items():
        if not isinstance(raw, dict):
            continue
        try:
            key_origin = normalize_origin(str(key))
        except BindingError:
            continue
        repository = raw.get("repository", key_origin)
        if normalize_origin(str(repository)) != key_origin:
            raise BindingError("registry key and repository disagree")
        sid = raw.get("session_id")
        if not isinstance(sid, str) or not sid:
            raise BindingError("registry session_id is missing")
        previous = session_ids.get(sid)
        if previous is not None and previous != key_origin:
            raise BindingError("session is referenced by multiple origins")
        session_ids[sid] = key_origin
        if key_origin == normalized:
            matches.append((key_origin, raw))
    if len(matches) != 1:
        raise BindingError("origin entry is missing or duplicated")
    return matches[0][1]


def validate_worktree_boundary(requested_root: str | pathlib.Path,
                               session_root: str | pathlib.Path,
                               enrolled_parent: str | pathlib.Path | None = None) -> bool:
    """Ensure a requested Git root is within the enrolled repository boundary."""
    requested = pathlib.Path(requested_root).resolve()
    anchor = pathlib.Path(session_root).resolve()
    # Without an explicit host-enrolled parent, only the registered root is
    # eligible; never infer a broad filesystem parent from the session root.
    boundary = pathlib.Path(enrolled_parent).resolve() if enrolled_parent else anchor
    try:
        requested.relative_to(boundary)
    except ValueError:
        return False
    return requested == anchor or requested.is_dir()
