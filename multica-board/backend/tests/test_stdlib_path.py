"""A queue.py in the process cwd must not replace the standard library."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest

BACKEND = Path(__file__).resolve().parents[1]
SHADOW = "SHADOW = True\n"


class StdlibPathTests(unittest.TestCase):
    def _run(self, cwd: Path, code: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(BACKEND)
        return subprocess.run(
            [sys.executable, "-c", textwrap.dedent(code)],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_cwd_queue_breaks_thread_pool_without_the_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "queue.py").write_text(SHADOW)
            result = self._run(
                Path(tmp),
                """
                from concurrent.futures import ThreadPoolExecutor
                ThreadPoolExecutor(max_workers=1).shutdown(wait=True)
                """,
            )
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("SimpleQueue", result.stderr)

    def test_collector_import_keeps_stdlib_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            shadow = Path(tmp) / "queue.py"
            shadow.write_text(SHADOW)
            result = self._run(
                Path(tmp),
                """
                import multica_board.collector
                import multica_board.usage
                import queue
                from concurrent.futures import ThreadPoolExecutor
                ThreadPoolExecutor(max_workers=1).shutdown(wait=True)
                assert hasattr(queue, "SimpleQueue"), queue.__file__
                assert queue.__file__
                assert not queue.__file__.startswith(%r)
                print("ok")
                """
                % str(shadow),
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ok", result.stdout)


if __name__ == "__main__":
    unittest.main()
