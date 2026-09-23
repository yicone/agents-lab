#!/usr/bin/env python3
import pathlib
import sys
import tempfile
from unittest.mock import patch

import host_setup


def test_setup_composes_exact_root_parent_and_session_registration():
    with tempfile.TemporaryDirectory() as directory:
        root = pathlib.Path(directory) / "main"
        parent = pathlib.Path(directory) / "worktrees"
        root.mkdir()
        parent.mkdir()
        calls = []

        def fake_step(script, args):
            calls.append((script, args))
            return {"status": "enrolled", "script": script}

        argv = [
            "host_setup.py",
            "--repo-root", str(root),
            "--worktree-parent", str(parent),
            "--session-root", str(root),
            "--session-id", "fixed-session",
        ]
        with patch.object(host_setup, "run_step", side_effect=fake_step), patch.object(sys, "argv", argv):
            assert host_setup.main() == 0

        assert [item[0] for item in calls] == ["host_enroll.py", "host_enroll.py", "session_enroll.py"]
        assert calls[0][1][0:2] == ["--repo-root", str(root.resolve())]
        assert calls[1][1][0:2] == ["--worktree-parent", str(parent.resolve())]
        assert "--worktree-parent" in calls[2][1]
        assert "--session-id" in calls[2][1]


if __name__ == "__main__":
    test_setup_composes_exact_root_parent_and_session_registration()
    print("ok")
