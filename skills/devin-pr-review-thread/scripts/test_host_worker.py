#!/usr/bin/env python3
import pathlib
import tempfile

from host_worker import acquire_worker_lock, claim_request, release_worker_lock, stable_failure


def test_request_claim_is_atomic_and_hidden_from_queue_glob():
    with tempfile.TemporaryDirectory() as directory:
        root = pathlib.Path(directory)
        request = root / "review-token.request.json"
        request.write_text("{}")
        claimed = claim_request(request)
        assert claimed is not None
        assert not request.exists()
        assert claimed.exists()
        claimed.unlink()


def test_worker_lock_rejects_second_consumer():
    with tempfile.TemporaryDirectory() as directory:
        root = pathlib.Path(directory)
        lock = acquire_worker_lock(root)
        try:
            try:
                acquire_worker_lock(root)
            except RuntimeError as exc:
                assert str(exc) == "worker_already_running"
            else:
                raise AssertionError("second worker unexpectedly acquired the queue lock")
        finally:
            release_worker_lock(lock)


def test_worker_fallback_preserves_request_identity():
    result = stable_failure({
        "repository_root": "/repo",
        "pr_number": 233,
        "round": 1,
        "authorization": {"head_sha": "abc"},
    }, "provider_output_invalid", "evidence-token")
    assert result["repository_root"] == "/repo"
    assert result["pr_number"] == 233
    assert result["head_sha"] == "abc"
    assert result["evidence_ref"] == "evidence-token"


if __name__ == "__main__":
    test_request_claim_is_atomic_and_hidden_from_queue_glob()
    test_worker_lock_rejects_second_consumer()
    test_worker_fallback_preserves_request_identity()
    print("ok")
