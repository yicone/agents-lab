#!/usr/bin/env python3
"""Black-box client for submitting and consuming host-side review requests."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import tempfile
import time
import uuid
from typing import Any

SCHEMA = "devin-host-review/v1"


def build_request(repo_root: str, pr_number: int, round_number: int | None,
                  timeout_seconds: int) -> dict[str, Any]:
    request: dict[str, Any] = {
        "schema": SCHEMA,
        "repository_root": str(pathlib.Path(repo_root).resolve()),
        "pr_number": pr_number,
        "timeout_seconds": timeout_seconds,
    }
    if round_number is not None:
        request["round"] = round_number
    return request


def atomic_write_json(path: pathlib.Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, separators=(",", ":"))
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def submit_request(queue: pathlib.Path, request: dict[str, Any]) -> tuple[pathlib.Path, pathlib.Path]:
    token = uuid.uuid4().hex
    request_path = queue / f"review-{token}.request.json"
    response_path = queue / f"review-{token}.response.json"
    atomic_write_json(request_path, request)
    return request_path, response_path


def timeout_response(request: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": SCHEMA,
        "status": "await-user",
        "failure_code": "queue_response_timeout",
        "repository_root": request["repository_root"],
        "pr_number": request["pr_number"],
        "head_sha": None,
        "round": request.get("round"),
        "findings_count": 0,
        "comments": [],
        "evidence_ref": None,
        "retryable": False,
    }


def wait_response(response_path: pathlib.Path, request: dict[str, Any], wait_seconds: float,
                 poll_seconds: float = 1.0) -> dict[str, Any]:
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        if response_path.exists():
            try:
                data = json.loads(response_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                return timeout_response(request) | {"failure_code": "response_invalid"}
            return data if isinstance(data, dict) else timeout_response(request) | {"failure_code": "response_invalid"}
        time.sleep(poll_seconds)
    return timeout_response(request)


def exit_code_for_result(result: dict[str, Any]) -> int:
    """Application statuses are data; do not make harnesses lose stdout."""
    return 0 if isinstance(result, dict) and result.get("schema") == SCHEMA else 2


def main() -> int:
    parser = argparse.ArgumentParser(description="Submit one minimal Devin host review request")
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--pr", required=True, type=int)
    parser.add_argument("--round", dest="round_number", type=int)
    parser.add_argument("--timeout-seconds", type=int, default=900)
    parser.add_argument("--wait-seconds", type=float, default=1800)
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--queue", default="/private/tmp/devin-host-review")
    args = parser.parse_args()
    if args.timeout_seconds < 30 or args.timeout_seconds > 1800:
        parser.error("--timeout-seconds must be between 30 and 1800")
    if args.wait_seconds <= 0 or args.poll_seconds <= 0:
        parser.error("--wait-seconds and --poll-seconds must be positive")
    request = build_request(args.repo_root, args.pr, args.round_number, args.timeout_seconds)
    _, response_path = submit_request(pathlib.Path(args.queue), request)
    result = wait_response(response_path, request, args.wait_seconds, args.poll_seconds)
    print(json.dumps(result, ensure_ascii=False))
    return exit_code_for_result(result)


if __name__ == "__main__":
    raise SystemExit(main())
