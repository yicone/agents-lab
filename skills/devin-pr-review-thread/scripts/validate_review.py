#!/usr/bin/env python3
"""Validate the safe subset of Devin review JSON used by the skill."""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

HEX40 = re.compile(r"^[0-9a-fA-F]{40}$")
SAFE_ID = re.compile(r"^[A-Za-z0-9._-]+$")
SECRET = re.compile(r"(?:gh[pousr]_\w{20,}|sk-[A-Za-z0-9_-]{16,}|Bearer\s+[A-Za-z0-9._~-]{16,}|-----BEGIN(?: [A-Z]+)? PRIVATE KEY-----)")
SHELL = re.compile(r"(?:^|\n)\s*(?:\$\s+|#!|(?:bash|sh|zsh|fish)\s+-c\s+|(?:curl|wget|rm\s+-|git\s+(?:push|reset)|gh\s+api)\b)")


def fail(message: str) -> int:
    print(f"invalid review: {message}", file=sys.stderr)
    return 2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("review_json", help="path to Devin's JSON response")
    parser.add_argument("--root", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--pr-number", required=True, type=int)
    parser.add_argument("--changed-lines", required=True, help="JSON object mapping changed paths to added RIGHT line numbers")
    args = parser.parse_args()
    try:
        data = json.loads(pathlib.Path(args.review_json).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return fail(str(exc))
    if data.get("schema") != "devin-pr-review/v1":
        return fail("schema must be devin-pr-review/v1")
    repository_root = data.get("repository_root")
    if not isinstance(repository_root, str) or not repository_root.strip():
        return fail("repository_root must be non-empty text")
    if pathlib.Path(repository_root).resolve() != pathlib.Path(args.root).resolve():
        return fail("repository_root mismatch")
    if data.get("session_id") != args.session_id:
        return fail("session_id mismatch")
    if data.get("pr_number") != args.pr_number:
        return fail("pr_number mismatch")
    if data.get("head_sha") != args.head_sha or not HEX40.fullmatch(data.get("head_sha", "")):
        return fail("head_sha mismatch or invalid")
    try:
        changed_lines = json.loads(pathlib.Path(args.changed_lines).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return fail(f"invalid changed-lines file: {exc}")
    if not isinstance(changed_lines, dict) or any(not isinstance(v, list) or any(not isinstance(n, int) for n in v) for v in changed_lines.values()):
        return fail("changed-lines must map paths to integer arrays")
    findings = data.get("findings")
    if not isinstance(findings, list):
        return fail("findings must be an array")
    seen = set()
    for finding in findings:
        if not isinstance(finding, dict):
            return fail("each finding must be an object")
        fid = finding.get("id")
        if not isinstance(fid, str) or not SAFE_ID.fullmatch(fid) or fid in seen:
            return fail("finding ids must be unique and safe")
        seen.add(fid)
        if finding.get("severity") not in {"blocker", "high", "medium", "low"}:
            return fail(f"{fid}: invalid severity")
        confidence = finding.get("confidence")
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0 <= confidence <= 1:
            return fail(f"{fid}: confidence must be between 0 and 1")
        path = finding.get("path")
        if not isinstance(path, str) or not path or path.startswith("/") or ".." in pathlib.PurePosixPath(path).parts:
            return fail(f"{fid}: path must be a relative repository path")
        if not isinstance(finding.get("line"), int) or isinstance(finding["line"], bool) or finding["line"] < 1:
            return fail(f"{fid}: line must be a positive integer")
        if finding.get("side") != "RIGHT":
            return fail(f"{fid}: side must be RIGHT")
        if path not in changed_lines or finding["line"] not in changed_lines[path]:
            return fail(f"{fid}: path/line is not an added RIGHT line in the PR diff")
        for key in ("title", "body"):
            value = finding.get(key)
            if not isinstance(value, str) or not value.strip() or "\x00" in value:
                return fail(f"{fid}: {key} must be non-empty text")
            if SECRET.search(value) or SHELL.search(value):
                return fail(f"{fid}: {key} contains a secret or executable shell text")
    print(json.dumps({"valid": True, "finding_count": len(findings)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
