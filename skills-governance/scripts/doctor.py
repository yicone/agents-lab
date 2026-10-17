#!/usr/bin/env python3
"""Check adapter directories and conservatively repair recorded broken links."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from broken_links import repair, resolved_target, scan

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INVENTORY = PACKAGE_ROOT / 'config/skills-inventory.json'
DEFAULT_REPO_ROOT = PACKAGE_ROOT.parent


def locate(value: str, root: Path) -> Path:
    candidate = Path(value).expanduser()
    return candidate if candidate.is_absolute() else root / candidate


def finding(kind, skill, path, target=None):
    return dict(kind=kind, skill=skill, path=str(path), target=target, repaired=False)


def inspect(inventory: dict, root: Path, do_repair: bool = False,
            adapter_roots: list[Path] | None = None) -> list[dict]:
    """Scan every adapter parent; null declared targets remain informational."""
    adapters = {}
    roots = set(adapter_roots or [])
    findings = []
    for skill in inventory.get('skills', []):
        for adapter in skill.get('adapters', []):
            location = adapter.get('path')
            if not isinstance(location, str):
                continue
            path = locate(location, root)
            adapters[path] = (skill.get('name'), adapter, skill)
            roots.add(path.parent)
            target = adapter.get('target')
            if adapter.get('kind') == 'declared_target':
                if target is None:
                    findings.append(finding('declared_target_unchecked', skill.get('name'), path))
                elif not locate(target, root).exists():
                    findings.append(finding('declared_target_missing', skill.get('name'), path, target))
            elif not path.exists() and not path.is_symlink():
                findings.append(finding('adapter_missing', skill.get('name'), path, target))

    broken = {link for directory in roots for link in scan(directory)}
    for path in sorted(broken):
        skill_name, adapter, skill = adapters.get(path, (None, None, None))
        if adapter is None and adapter_roots is not None:
            matches = [entry for entry in inventory.get('skills', [])
                       if entry.get('name') == path.name]
            if len(matches) == 1:
                skill = matches[0]
                skill_name = skill['name']
                adapter = {'kind': 'symlink', 'target': None}
        item = finding('broken_symlink' if adapter else 'orphan_broken_symlink',
                       skill_name, path, resolved_target(path))
        if do_repair and adapter and adapter.get('kind') == 'symlink':
            canonical = (skill.get('authority') or {}).get('canonical_path')
            if skill.get('status') in {'retired', 'deprecated', 'archived'}:
                path.unlink()
                item['repaired'] = True
                item['action'] = 'removed'
                findings.append(item)
                continue
            target = adapter.get('target')
            if canonical:
                destination = locate(canonical, root)
                target = str(destination) if (destination / 'SKILL.md').is_file() else None
            if isinstance(target, str):
                destination = locate(target, path.parent)
                if destination.exists():
                    repair(path, str(Path(target).expanduser()))
                    item['repaired'] = path.exists()
        findings.append(item)
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory', type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument('--repo-root', type=Path, default=DEFAULT_REPO_ROOT)
    parser.add_argument('--repair', action='store_true')
    parser.add_argument('--format', choices=('json', 'text'), default='json')
    args = parser.parse_args(argv)
    inventory = json.loads(args.inventory.expanduser().read_text(encoding='utf-8'))
    findings = inspect(inventory, args.repo_root.expanduser().resolve(), args.repair)
    remaining = [item for item in findings
                 if not item['repaired'] and item['kind'] != 'declared_target_unchecked']
    if args.format == 'text':
        if not findings:
            print('No adapter link problems found.')
        for item in findings:
            suffix = ' [repaired]' if item['repaired'] else ''
            target = item['target'] if item['target'] is not None else '(not bound; unchecked)'
            print(f"{item['kind']}: {item['path']} -> {target}{suffix}")
    else:
        print(json.dumps(dict(broken=findings, count=len(remaining),
                              repaired=sum(item['repaired'] for item in findings)), indent=2))
    return 1 if remaining else 0


if __name__ == '__main__':
    sys.exit(main())
