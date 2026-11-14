"""Build special fixture entries in a disposable copy: Git cannot store FIFOs."""
import os
import shutil
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
REPO = PROJECT.parent
sys.path.insert(0, str(PROJECT))
FIXTURE = PROJECT / "tests/fixtures/box-home"


def make_fixture(destination):
    home = Path(destination) / "box-home"
    shutil.copytree(FIXTURE, home, symlinks=True)
    workflow = home / "sand-data/workflows/example"
    os.mkfifo(workflow / "pipe")
    return home
