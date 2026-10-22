"""Dispatcher identity guard: writes only happen as the task-scoped 副调度 agent."""

from __future__ import annotations

from . import config


def check_dispatcher_identity(env: dict) -> tuple[bool, str]:
    """ok only inside a daemon-injected agent task running as 副调度.

    Token is checked for presence only — never read, logged, or copied.
    """
    if env.get("MULTICA_PROFILE") or env.get("MULTICA_CLI_PROFILE"):
        return False, "profile flag/env in use"
    if not env.get("MULTICA_TASK_ID"):
        return False, "MULTICA_TASK_ID missing (not an agent task)"
    if env.get("MULTICA_AGENT_ID") != config.DISPATCHER_AGENT_ID:
        return False, "MULTICA_AGENT_ID is not the dispatcher agent"
    if not env.get("MULTICA_TOKEN"):
        return False, "MULTICA_TOKEN missing"
    return True, "ok"


def verify_comment_author(comment: dict) -> bool:
    return (
        comment.get("author_type") == "agent"
        and comment.get("author_id") == config.DISPATCHER_AGENT_ID
    )
