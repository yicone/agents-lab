#!/usr/bin/env python3
import tempfile, pathlib, json, subprocess, sys
from host_provider import is_unresolvable_review_comment, response, validate_request


class FakeResult:
    def __init__(self, returncode, stderr="", stdout=""):
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = stdout


def test_failure_response_has_stable_failure_code_field():
    result = response("await-user", {"repository_root": "/tmp/repo", "pr_number": 232, "round": 2}, failure_code="permission_or_process_failure", evidence_ref="evidence-token")
    assert result["failure_code"] == "permission_or_process_failure"
    assert result["evidence_ref"] == "evidence-token"

def test_unresolvable_github_line_is_safe_to_drop():
    result = FakeResult(1, stderr='Validation Failed (HTTP 422) could not be resolved')
    assert is_unresolvable_review_comment(result)
    assert not is_unresolvable_review_comment(FakeResult(1, stderr='authentication failed'))


def test_control_record_is_not_accepted_as_provider_request():
    process = subprocess.run(
        [sys.executable, str(pathlib.Path(__file__).with_name("host_provider.py"))],
        input=json.dumps({"schema": "pr-review-round/v1", "round": 2}),
        text=True,
        capture_output=True,
    )
    result = json.loads(process.stdout)
    assert result["failure_code"] == "control_record_not_provider_request"

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
    test_failure_response_has_stable_failure_code_field(); test_control_record_is_not_accepted_as_provider_request(); test_rejects_self_asserted_authorization(); test_accepts_host_bound_authorization(); test_missing_allowlist_is_operator_state(); test_cross_origin_allowlist_is_invalid_request(); print("ok")
