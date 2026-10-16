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


SESSION_LAYOUT_EXACT_ROOT = "exact-root"
SESSION_LAYOUT_PARENT_OF_REPOSITORY = "parent-of-repository"
SESSION_LAYOUTS = {SESSION_LAYOUT_EXACT_ROOT, SESSION_LAYOUT_PARENT_OF_REPOSITORY}


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
    if any(isinstance(key, str) and key.startswith("/") for key in registry):
        raise BindingError("legacy root-keyed registry requires migration")
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
    # The registered main workspace may live beside the enrolled worktree
    # parent; it is itself always an eligible review root.
    if requested == anchor:
        return True
    # Without an explicit host-enrolled parent, only the registered root is
    # eligible; never infer a broad filesystem parent from the session root.
    boundary = pathlib.Path(enrolled_parent).resolve() if enrolled_parent else anchor
    try:
        requested.relative_to(boundary)
    except ValueError:
        return False
    return requested.is_dir()


def validate_session_layout(requested_root: str | pathlib.Path,
                            session_root: str | pathlib.Path,
                            enrolled_parent: str | pathlib.Path | None,
                            layout: str = SESSION_LAYOUT_EXACT_ROOT,
                            *, repository_root: str | pathlib.Path | None = None) -> bool:
    """Validate the repository target against the session workspace layout.

    ``exact-root`` preserves the original binding.  ``parent-of-repository``
    permits a registered session workspace to be the immediate parent of the
    main repository, plus worktrees below the explicitly enrolled parent.
    The parent layout is opt-in so a broad filesystem parent is never inferred
    from a path alone.
    """
    if layout not in SESSION_LAYOUTS:
        return False
    record = {"session_root": str(session_root), "session_layout": layout}
    if repository_root is not None:
        record["repository_root"] = str(repository_root)
    if enrolled_parent is not None:
        record["enrolled_parent"] = str(enrolled_parent)
    try:
        binding = resolve_session_binding(record)
    except BindingError:
        return False
    return validate_worktree_boundary(requested_root, binding["repository_root"], binding["enrolled_parent"])


def resolve_session_binding(entry: dict[str, Any]) -> dict[str, Any]:
    """Resolve explicit roots without broadening an old v2 binding."""
    layout = entry.get("session_layout", SESSION_LAYOUT_EXACT_ROOT)
    if not isinstance(layout, str) or layout not in SESSION_LAYOUTS:
        raise BindingError("session_layout_invalid")

    def absolute_path(value: Any) -> pathlib.Path:
        if not isinstance(value, str) or not value or not pathlib.Path(value).is_absolute():
            raise BindingError("session_root_mismatch")
        return pathlib.Path(value).resolve()

    session = absolute_path(entry.get("session_root"))
    if layout == SESSION_LAYOUT_PARENT_OF_REPOSITORY and "repository_root" not in entry:
        raise BindingError("session_repository_root_missing")
    repository = absolute_path(entry.get("repository_root", str(session)))
    enrolled = absolute_path(entry["enrolled_parent"]) if entry.get("enrolled_parent") is not None else None
    if layout == SESSION_LAYOUT_EXACT_ROOT:
        if repository != session:
            raise BindingError("session_root_mismatch")
    elif repository.parent != session or repository == session:
        raise BindingError("session_root_outside_registered_boundary")
    if layout == SESSION_LAYOUT_PARENT_OF_REPOSITORY and enrolled is not None:
        if enrolled == session or not enrolled.is_relative_to(session):
            raise BindingError("session_root_outside_registered_boundary")
    common_dir = entry.get("repository_common_dir")
    if common_dir is not None and (not isinstance(common_dir, str) or not common_dir or not pathlib.Path(common_dir).is_absolute()):
        raise BindingError("repository_common_dir_invalid")
    return {"repository_root": str(repository), "session_root": str(session),
            "session_layout": layout, "enrolled_parent": str(enrolled) if enrolled else None,
            "repository_common_dir": str(pathlib.Path(common_dir).resolve()) if common_dir else None}
