#!/usr/bin/env python3
"""Validate a bounded PR-review round decision record."""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys


HEX40 = re.compile(r"^[0-9a-fA-F]{40}$")
REPOSITORY = re.compile(r"^[^/\s]+/[^/\s]+$")
SAFE_ID = re.compile(r"^[A-Za-z0-9._-]+$")
CLASSES = {"must-fix", "should-fix", "product-decision", "ignore"}
POST_ACTIONS = {"fix-and-rereview", "fix-and-stop", "stop", "follow-up", "await-user"}
EXCEPTIONS = {"must-fix", "review-fix-regression", "accepted-should-fix"}


def fail(message: str) -> int:
    print(f"invalid review round: {message}", file=sys.stderr)
    return 2


def nonempty_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("record_json", help="path to the review-round JSON record")
    args = parser.parse_args()
    try:
        data = json.loads(pathlib.Path(args.record_json).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return fail(str(exc))
    if not isinstance(data, dict) or data.get("schema") != "pr-review-round/v1":
        return fail("schema must be pr-review-round/v1")

    pr = data.get("pr")
    if not isinstance(pr, dict) or not isinstance(pr.get("repository"), str) or not REPOSITORY.fullmatch(pr["repository"]):
        return fail("pr.repository must be owner/repo")
    if not isinstance(pr.get("number"), int) or isinstance(pr["number"], bool) or pr["number"] < 1:
        return fail("pr.number must be a positive integer")
    if not isinstance(pr.get("head_sha"), str) or not HEX40.fullmatch(pr["head_sha"]):
        return fail("pr.head_sha must be a 40-hex SHA")
    round_number = data.get("round")
    if not isinstance(round_number, int) or isinstance(round_number, bool) or round_number < 1:
        return fail("round must be a positive integer")
    reviewer = data.get("reviewer")
    if not isinstance(reviewer, dict) or not all(nonempty_text(reviewer.get(key)) for key in ("provider", "session_id", "model")):
        return fail("reviewer provider, session_id, and model must be non-empty text")
    if not SAFE_ID.fullmatch(reviewer["session_id"]):
        return fail("reviewer.session_id contains unsafe characters")

    phase = data.get("phase")
    decision = data.get("decision")
    if phase not in {"run", "post-triage"} or not isinstance(decision, dict):
        return fail("phase must be run or post-triage and decision must be an object")
    action = decision.get("action")
    if not nonempty_text(decision.get("reason")):
        return fail("decision.reason must be non-empty text")
    if phase == "run":
        if action != "run":
            return fail("run phase requires decision.action=run")
        if "findings" in data or "validation" in decision or "exception" in decision:
            return fail("run phase cannot include findings, validation, or exception")
    else:
        if action not in POST_ACTIONS:
            return fail("post-triage action is invalid")
        findings = data.get("findings")
        if not isinstance(findings, dict) or set(findings) != CLASSES:
            return fail("findings must contain exactly the four classification buckets")
        ids: set[str] = set()
        for category in CLASSES:
            entries = findings[category]
            if not isinstance(entries, list):
                return fail(f"findings.{category} must contain unique safe identifiers")
            if any(not isinstance(fid, str) or not SAFE_ID.fullmatch(fid) or fid in ids for fid in entries):
                return fail(f"findings.{category} must contain unique safe identifiers")
            if len(entries) != len(set(entries)):
                return fail(f"findings.{category} must contain unique safe identifiers")
            ids.update(entries)
        validation = decision.get("validation")
        if action == "fix-and-rereview":
            if not isinstance(validation, list) or not validation or any(not nonempty_text(item) for item in validation):
                return fail("fix-and-rereview requires non-empty validation evidence")
            exception = decision.get("exception")
            if round_number >= 2 and exception not in EXCEPTIONS:
                return fail("third and later fix-and-rereview requires a bounded exception")
            if round_number < 2 and exception is not None:
                return fail("exception is allowed only for third and later re-reviews")
        elif "exception" in decision:
            return fail("exception is only allowed for fix-and-rereview")
    print(json.dumps({"valid": True, "round": round_number, "phase": phase, "action": action}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
