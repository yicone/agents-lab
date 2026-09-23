#!/usr/bin/env python3
import datetime as dt
import json
import os
import pathlib
import tempfile

from cleanup import plan_cleanup


def test_cleanup_preserves_referenced_recent_and_unconsumed_authorizations():
    with tempfile.TemporaryDirectory() as directory:
        root = pathlib.Path(directory)
        evidence = root / "evidence"
        queue = root / "queue"
        evidence.mkdir()
        queue.mkdir()
        ledger = root / "ledger.json"
        auth = root / "round-authorizations.json"
        now = dt.datetime(2026, 9, 23, tzinfo=dt.timezone.utc)
        old_time = (now - dt.timedelta(days=45)).timestamp()

        referenced = evidence / "referenced-token.json"
        referenced.write_text("{}")
        os.utime(referenced, (old_time, old_time))
        unreferenced = evidence / "unreferenced-token.json"
        unreferenced.write_text("{}")
        os.utime(unreferenced, (old_time, old_time))
        recent = evidence / "recent-token.json"
        recent.write_text("{}")
        ledger.write_text(json.dumps({"run": {"response": {"evidence_ref": "referenced-token"}}}))
        response = queue / "review-old.response.json"
        response.write_text(json.dumps({"evidence_ref": "referenced-token"}))
        os.utime(response, (old_time, old_time))
        auth.write_text(json.dumps({
            "old-unused": {"used": False, "issued_at": "2026-07-01T00:00:00+00:00"},
            "recent-unused": {"used": False, "issued_at": "2026-09-23T00:00:00+00:00"},
        }))

        result = plan_cleanup(evidence, queue, auth, ledger, 30, False, now=now)
        assert str(unreferenced) in result["candidates"]["evidence"]
        assert str(referenced) in result["protected"]["evidence"]
        assert str(recent) in result["protected"]["evidence"]
        assert "old-unused" in result["protected"]["authorization_ids"]
        assert "recent-unused" in result["protected"]["authorization_ids"]

        expired = plan_cleanup(evidence, queue, auth, ledger, 30, True, now=now)
        assert "old-unused" in expired["candidates"]["authorization_ids"]
        assert "recent-unused" not in expired["candidates"]["authorization_ids"]


if __name__ == "__main__":
    test_cleanup_preserves_referenced_recent_and_unconsumed_authorizations()
    print("ok")
