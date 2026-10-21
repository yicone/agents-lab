"""Keep the process cwd from replacing standard-library modules.

The login-item launcher starts the board with its working directory set to
/tmp. Python puts that directory first on sys.path, so a queue.py left there
is imported instead of the standard library and ThreadPoolExecutor crashes.
"""

from __future__ import annotations

import os
import sys


def prefer_stdlib_imports() -> None:
    """Drop the current directory from sys.path before stdlib imports."""
    cwd = os.path.abspath(os.getcwd())
    sys.path[:] = [entry for entry in sys.path if entry and os.path.abspath(entry) != cwd]
