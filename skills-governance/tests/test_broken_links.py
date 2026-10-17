import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))
from broken_links import repair, scan


def test_scan_and_repair(tmp_path):
    root = tmp_path / "adapters"
    root.mkdir()
    target = tmp_path / "target"
    target.mkdir()
    link = root / "skill"
    link.symlink_to(tmp_path / "missing")
    assert scan(root) == [link]
    repair(link, str(target))
    assert scan(root) == []
