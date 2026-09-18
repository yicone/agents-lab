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


def fail(message: str) -> int:
    print(f"invalid review: {message}", file=sys.stderr)
    return 2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("review_json", help="path to Devin's JSON response")
    parser.add_argument("--root", required=True)
    parser.add_argument("--head-sha", required=True)
    args = parser.parse_args()
    try:
        data = json.loads(pathlib.Path(args.review_json).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return fail(str(exc))
    if data.get("schema") != "devin-pr-review/v1":
        return fail("schema must be devin-pr-review/v1")
    if pathlib.Path(data.get("repository_root", "")).resolve() != pathlib.Path(args.root).resolve():
        return fail("repository_root mismatch")
    if data.get("head_sha") != args.head_sha or not HEX40.fullmatch(data.get("head_sha", "")):
        return fail("head_sha mismatch or invalid")
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
        for key in ("title", "body"):
            value = finding.get(key)
            if not isinstance(value, str) or not value.strip() or "\x00" in value:
                return fail(f"{fid}: {key} must be non-empty text")
    print(json.dumps({"valid": True, "finding_count": len(findings)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
