#!/usr/bin/env python3
import json
import pathlib
import stat
import tempfile

from queue_client import build_request, exit_code_for_result, submit_request, wait_response


def test_minimal_request_and_secure_atomic_write():
    with tempfile.TemporaryDirectory() as directory:
        request = build_request(directory, 232, 2, 900)
        request_path, response_path = submit_request(pathlib.Path(directory), request)
        assert request == json.loads(request_path.read_text())
        assert request_path.name.endswith(".request.json")
        assert not response_path.exists()
        assert stat.S_IMODE(request_path.stat().st_mode) == 0o600
        assert set(request) == {"schema", "repository_root", "pr_number", "round", "timeout_seconds"}


def test_response_polling_and_timeout_are_stable():
    with tempfile.TemporaryDirectory() as directory:
        request = build_request(directory, 1, None, 900)
        response_path = pathlib.Path(directory) / "response.json"
        for stable_status in ("no-findings", "review-published"):
            response_path.write_text(json.dumps({"schema": "devin-host-review/v1", "status": stable_status}))
            assert wait_response(response_path, request, 0.1)["status"] == stable_status
        timed_out = wait_response(pathlib.Path(directory) / "missing.json", request, 0.01, 0.005)
        assert timed_out["failure_code"] == "queue_response_timeout"
        assert timed_out["evidence_ref"] is None


def test_application_await_user_is_still_a_successful_cli_transport():
    assert exit_code_for_result({"schema": "devin-host-review/v1", "status": "await-user"}) == 0
    assert exit_code_for_result({"schema": "devin-host-review/v1", "status": "review-published"}) == 0
    assert exit_code_for_result({"status": "malformed"}) == 2


if __name__ == "__main__":
    test_minimal_request_and_secure_atomic_write()
    test_response_polling_and_timeout_are_stable()
    test_application_await_user_is_still_a_successful_cli_transport()
    print("ok")
