#!/usr/bin/env python3
"""Inspect inventory adapter links and optionally repair broken links."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from broken_links import repair, scan

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INVENTORY = PACKAGE_ROOT / "config" / "skills-inventory.json"
DEFAULT_REPO_ROOT = PACKAGE_ROOT.parent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--repo-root", type=Path, default=DEFAULT_REPO_ROOT)
    parser.add_argument("--repair", action="store_true", help="repair links when their recorded target exists")
    args = parser.parse_args(argv)
    inventory = json.loads(args.inventory.expanduser().read_text(encoding="utf-8"))
    repo_root = args.repo_root.expanduser().resolve()
    findings = []
    for skill in inventory.get("skills", []):
        for adapter in skill.get("adapters", []):
            if adapter.get("kind") != "symlink":
                continue
            location = adapter.get("path", "")
            if location.startswith("~") or Path(location).is_absolute():
                path = Path(location).expanduser()
            else:
                path = repo_root / location
            if path in set(scan(path.parent)):
                item = {"skill": skill.get("name"), "path": str(path), "target": adapter.get("target"), "repaired": False}
                if args.repair and isinstance(adapter.get("target"), str) and Path(adapter["target"]).expanduser().exists():
                    repair(path, adapter["target"])
                    item["repaired"] = True
                findings.append(item)
    print(json.dumps({"broken": findings, "count": len(findings), "repaired": sum(x["repaired"] for x in findings)}, indent=2))
    return 1 if findings and not args.repair else 0


if __name__ == "__main__":
    sys.exit(main())
