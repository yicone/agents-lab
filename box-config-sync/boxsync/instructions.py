"""Load the repository's canonical instructions code without changing sys.path."""
import importlib.util
import sys
from pathlib import Path

_PATH = Path(__file__).resolve().parents[2] / "instructions-governance/scripts/diff_instructions.py"
_NAME = "diff_instructions"
if _NAME in sys.modules:
    _module = sys.modules[_NAME]
    if Path(_module.__file__).resolve() != _PATH:
        raise ImportError("unexpected instructions implementation")
else:
    _spec = importlib.util.spec_from_file_location(_NAME, _PATH)
    _module = importlib.util.module_from_spec(_spec)
    sys.modules[_NAME] = _module
    try:
        _spec.loader.exec_module(_module)
    except Exception:
        del sys.modules[_NAME]
        raise

instructions_sha256 = _module.instructions_sha256
SECRET_PATTERNS = _module.SECRET_PATTERNS
