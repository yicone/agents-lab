#!/usr/bin/env python3
"""Run only from the owner's ordinary terminal, never an agent task shell."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from multica_board.stdlib_path import prefer_stdlib_imports

prefer_stdlib_imports()
from multica_board.owner_agents import main

if __name__ == "__main__":
    raise SystemExit(main())
