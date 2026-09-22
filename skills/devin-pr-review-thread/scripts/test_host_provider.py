#!/usr/bin/env python3
import tempfile, pathlib, json, subprocess, sys
from host_provider import validate_request

def test_rejects_self_asserted_authorization():
    with tempfile.TemporaryDirectory() as d:
        req={"schema":"devin-host-review/v1","repository_root":d,"pr_number":1,"round":1,"authorization":{"action":"run","authorization_id":"x","head_sha":"a"},"timeout_seconds":900}
        assert validate_request(req,{str(pathlib.Path(d).resolve()): "o/r"},{}) == "invalid-request"

def test_accepts_host_bound_authorization():
    with tempfile.TemporaryDirectory() as d:
        root=str(pathlib.Path(d).resolve())
        req={"schema":"devin-host-review/v1","repository_root":root,"pr_number":1,"round":1,"authorization":{"action":"run","authorization_id":"x","head_sha":"a"},"timeout_seconds":900}
        auth={"x":{"repository_root":root,"pr_number":1,"round":1,"head_sha":"a"}}
        assert validate_request(req,{root: "o/r"},auth) is None

def test_missing_allowlist_is_operator_state():
    with tempfile.TemporaryDirectory() as d:
        root=str(pathlib.Path(d).resolve())
        req={"schema":"devin-host-review/v1","repository_root":root,"pr_number":1,"round":1,"authorization":{"action":"run","authorization_id":"x","head_sha":"a"},"timeout_seconds":900}
        assert validate_request(req, {}, {"x":{"repository_root":root,"pr_number":1,"round":1,"head_sha":"a"}}) == "await-user"

def test_cross_origin_allowlist_is_invalid_request():
    with tempfile.TemporaryDirectory() as d:
        root=str(pathlib.Path(d).resolve())
        subprocess.run(["git", "-C", root, "init", "-q"])
        subprocess.run(["git", "-C", root, "remote", "add", "origin", "git@github.com:actual/repo.git"])
        req={"schema":"devin-host-review/v1","repository_root":root,"pr_number":1,"round":1,"authorization":{"action":"run","authorization_id":"x","head_sha":"a"},"timeout_seconds":900}
        assert validate_request(req,{root: "other/repo"},{"x":{"repository_root":root,"pr_number":1,"round":1,"head_sha":"a"}}) == "invalid-request"


if __name__ == "__main__":
    test_rejects_self_asserted_authorization(); test_accepts_host_bound_authorization(); test_missing_allowlist_is_operator_state(); test_cross_origin_allowlist_is_invalid_request(); print("ok")
