#!/usr/bin/env python3
import tempfile, pathlib, json, subprocess, sys
from host_provider import build_devin_args, is_transport_error, is_unresolvable_review_comment, parse_changed_lines, response, review_execution_root, validate_request


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

def test_review_execution_uses_registered_root_not_request_worktree():
    with tempfile.TemporaryDirectory() as canonical, tempfile.TemporaryDirectory() as request_root:
        assert review_execution_root(request_root, {"registered_root": canonical}) == canonical
        assert review_execution_root(request_root, {"registered_root": "/missing"}) == request_root

def test_rest_patch_parser_tracks_added_right_lines():
    patch = "diff --git a/file.py b/file.py\n+++ b/file.py\n@@ -1,2 +1,3 @@\n old\n+new\n+newer\n"
    assert parse_changed_lines(patch) == {"file.py": [2, 3]}

def test_patch_transport_classifier_is_narrow():
    assert is_transport_error("unexpected EOF")
    assert is_transport_error("TLS handshake timeout")
    assert not is_transport_error("HTTP 404 Not Found")

def test_large_prompt_uses_prompt_file_not_argv():
    args = build_devin_args("succinct-avenue", None, "/tmp/prompt.txt")
    assert "--prompt-file" in args
    assert "/tmp/prompt.txt" in args
    assert "-p" in args
    assert not any("VERIFIED PR PATCH" in arg for arg in args)


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
