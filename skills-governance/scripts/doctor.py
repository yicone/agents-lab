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


def adapter_path(path: Path) -> Path:
    """Resolve directory aliases without dereferencing the adapter itself."""
    return path.parent.resolve() / path.name


def inspect(inventory: dict, root: Path, do_repair: bool = False,
            adapter_roots: list[Path] | None = None) -> list[dict]:
    """Inspect unique adapter paths and isolate failures to individual links."""
    root = root.resolve()
    adapters = {}
    roots = {directory.expanduser().resolve() for directory in adapter_roots or []}
    findings = []
    for skill in inventory.get('skills', []):
        for adapter in skill.get('adapters', []):
            location = adapter.get('path')
            if not isinstance(location, str):
                continue
            path = adapter_path(locate(location, root))
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

    broken = {adapter_path(link) for directory in roots for link in scan(directory)}
    for path in sorted(broken):
        skill_name, adapter, skill = adapters.get(path, (None, None, None))
        if adapter is None and adapter_roots is not None and path.parent in roots:
            matches = [entry for entry in inventory.get('skills', [])
                       if entry.get('name') == path.name]
            if len(matches) == 1:
                skill = matches[0]
                skill_name = skill['name']
                adapter = {'kind': 'symlink', 'target': None}
        item = finding('broken_symlink' if adapter else 'orphan_broken_symlink',
                       skill_name, path, resolved_target(path))
        if adapter is None or adapter.get('kind') != 'symlink':
            item['reason'] = 'orphan'
        else:
            status = skill.get('status', 'unknown')
            canonical = (skill.get('authority') or {}).get('canonical_path')
            target = adapter.get('target')
            if canonical:
                destination = locate(canonical, root)
                if (destination / 'SKILL.md').is_file():
                    target = str(destination)
            if status == 'retired':
                item['reason'] = 'retired'
            elif status not in {'active', 'staged'}:
                item['reason'] = f'non active status: {status}'
            elif not isinstance(target, str) or not locate(target, path.parent).exists():
                item['reason'] = 'no canonical directory or existing recorded target'
            else:
                item['reason'] = 'repairable'
            if do_repair:
                try:
                    if status == 'retired':
                        if not path.is_symlink() or path.exists():
                            raise ValueError(f'not a broken symlink: {path}')
                        path.unlink()
                        item.update(repaired=True, action='removed')
                    elif item['reason'] == 'repairable':
                        repair(path, str(Path(target).expanduser()))
                        item['repaired'] = path.exists()
                        item['target'] = resolved_target(path)
                        if not item['repaired']:
                            item['reason'] = 'target unavailable after repair'
                except (OSError, ValueError) as error:
                    item['repaired'] = False
                    item['reason'] = f'repair failed: {error}'
        findings.append(item)
    return findings


def text_finding(item: dict) -> str:
    action = item.get('action', 'repaired' if item['repaired'] else 'unresolved')
    if item['kind'] == 'declared_target_unchecked':
        action = 'unchecked'
    target = item['target'] if item['target'] is not None else '(not bound; unchecked)'
    reason = f" ({item['reason']})" if not item['repaired'] and item.get('reason') else ''
    return f"{item['kind']}: {item['path']} -> {target} [{action}]{reason}"


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
            print(text_finding(item))
    else:
        print(json.dumps(dict(broken=findings, count=len(remaining),
                              repaired=sum(item['repaired'] for item in findings)), indent=2))
    return 1 if remaining else 0


if __name__ == '__main__':
    sys.exit(main())
