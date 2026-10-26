"""ACM-591 P1–P9 shadow inspection. No platform writes or implicit exemptions.

Run: PYTHONPATH=multica-board/backend python3 -m multica_board.governance_scan
  --workspace-id ID --output REPORT.json [--exceptions approved.json]
Atlas reads <db-stem>-governance.json when explicitly generated for that DB.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
from datetime import datetime, timezone

from .layers import catalog_from_rows, _load_properties, _tokens, is_open

CATEGORIES = {'dispatch_inbox', 'weekly_triage', 'getting_started', 'operations_shell', 'migrated_history'}
LABELS = {f'L{i} {name}' for i, name in enumerate(('资源', '执行', '能力', '协作', '感知与决策'), 1)}
RULES = dict(enumerate(('MISSING_PROJECT', 'WRONG_PROJECT_CANDIDATE', 'ACTIVE_ON_COMPLETED_PROJECT',
    'MISSING_ALL_PROPERTIES', 'PARTIAL_PROPERTIES', 'INVALID_PROPERTY_VALUE',
    'OUT_OF_SCOPE_PROPERTIES', 'INVALID_EXCEPTION', 'ARCHIVE_CONFLICT'), 1))
# Explicit boundaries only; ambiguous matches remain candidates.
BOUNDARIES = (
    ('Agent Atlas', r'agent atlas|multica.board|skills? 治理|状态桥'),
    ('开源项目观察', r'开源.*(?:观察|调研)|(?:观察|调研).*开源'),
    ('agent-helpers', r'agent.helpers'),
    ('业务站迁入 Multica', r'业务站.*迁入|(?:爱买 AI|萌芽星球).*multica'),
)


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def snapshot_path(db):
    p = Path(db)
    return p.with_name(p.stem + '-governance.json')


def inspect(issues, projects, definitions, *, workspace_id, exceptions=None, detected_at=None):
    import re
    at = detected_at or stamp()
    now = datetime.fromisoformat(at.replace('Z', '+00:00'))
    catalog = catalog_from_rows(definitions)
    if not catalog.ready:
        raise ValueError('three-property catalog unavailable or incompatible')
    props = (catalog.primary, catalog.involved, catalog.governance)
    project_by = {p['id']: p for p in projects}
    findings = []
    exceptions = exceptions or {}
    for issue in sorted(issues, key=lambda i: i['id']):
        if issue.get('workspace_id') != workspace_id:
            raise ValueError('issue workspace mismatch')
        iid = issue['id']
        project = project_by.get(issue.get('project_id'), {})
        title = issue.get('title') or ''
        text = title + '\n' + (issue.get('description') or '')
        raw = _load_properties(issue)
        values = {p.name: raw.get(p.id) for p in props}
        present = [v is not None and v != '' and v != [] for v in values.values()]
        open_ = is_open(issue)
        ended_project = project.get('status') in {'completed', 'cancelled', 'canceled'} or '已迁出' in project.get('title', '')
        entry_raw = exceptions.get(iid)
        entry = entry_raw if isinstance(entry_raw, dict) else None
        valid = False
        reason = 'invalid_schema' if entry_raw is not None and entry is None else 'missing'
        if entry:
            try:
                expiry = datetime.fromisoformat(entry['review_at'].replace('Z', '+00:00'))
                valid = (entry['category'] in CATEGORIES and bool(entry['reason']) and bool(entry['owner'])
                    and now < expiry and issue.get('status') in entry['allowed_statuses']
                    and issue.get('project_id') in entry['allowed_projects']
                    and entry['content_sha256'] == hashlib.sha256(text.encode()).hexdigest())
                if entry['category'] == 'migrated_history' and open_:
                    valid = False
                reason = 'approved' if valid else 'expired_or_conflicting'
            except (KeyError, TypeError, ValueError):
                reason = 'invalid_schema'
        # Keywords are evidence of a possible exception, never authorization.
        hint = next((c for c, pattern in (
            ('dispatch_inbox', r'副调度收件箱|事件转发|提醒台账'),
            ('weekly_triage', r'周度\s*triage|周计划|backlog 盘点'),
            ('operations_shell', r'纯运维壳|临时 smoke test|一次性放行验证'),
        ) if re.search(pattern, title, re.I)), None)
        if project.get('title') == 'Getting Started':
            hint = 'getting_started'
            # Frozen directory exception is authoritative, unlike title keywords.
            valid = not open_ or issue.get('status') in {'todo', 'in_progress', 'in_review', 'blocked'}
            reason = 'frozen_project_directory'
        historical = ended_project and not open_
        if historical and '已迁出' in project.get('title', '') and not entry:
            valid, reason = True, 'frozen_migrated_history'
            hint = 'migrated_history'
        whitelist = {'category': (entry or {}).get('category') or hint, 'valid': valid, 'reason': reason}
        candidates = []
        for name, pattern in BOUNDARIES:
            match = re.search(pattern, text, re.I)
            if match:
                candidates.append({'project_id': next((p['id'] for p in projects if p.get('title') == name and p.get('status') not in {'completed', 'cancelled'}), None),
                                   'project_title': name, 'evidence': match.group(0), 'title_match': bool(re.search(pattern, title, re.I))})

        def add(n, current, candidate=None, confidence='high', evidence=None):
            content = {'issue_id': iid, 'rule': f'P{n}', 'current': current, 'candidate': candidate,
                       'confidence': confidence, 'evidence': evidence or [], 'whitelist': whitelist}
            fingerprint = hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            findings.append({**content, 'identifier': issue.get('identifier'), 'title': title,
                'code': RULES[n], 'priority': 'high' if open_ and confidence == 'high' else 'low',
                'detected_at': at, 'source_updated_at': issue.get('updated_at'), 'fingerprint': fingerprint})

        if not issue.get('project_id') and open_ and not valid:
            add(1, None, candidates or None, 'high' if len(candidates) == 1 and candidates[0]['project_id'] and candidates[0]['title_match'] else 'low',
                [c['evidence'] for c in candidates] or ['No unambiguous project boundary; manual classification required'])
        if project.get('status') in {'in_progress', 'active', 'planned', 'paused'}:
            other = [c for c in candidates if c['project_title'] != project.get('title')]
            if other:
                add(2, issue.get('project_id'), other, 'medium', [c['evidence'] for c in other])
        if open_ and ended_project and not (valid and whitelist['category'] == 'getting_started'):
            add(3, {'project': project.get('title'), 'status': issue.get('status')}, evidence=['Open issue on completed/migrated project'])
        outside_value = catalog.primary.option_names.get(str(raw.get(catalog.primary.id)), raw.get(catalog.primary.id)) == '不在地图'
        on_map = not valid and not hint and not historical and not outside_value
        if on_map and not any(present):
            add(4, values, evidence=['All three own properties unset; inheritance is not a scope assertion'])
        if on_map and 0 < sum(present) < 3:
            add(5, values, evidence=['Missing fields: ' + ', '.join(p.name for p, yes in zip(props, present) if not yes)])
        invalid = []
        for p, yes in zip(props, present):
            if not yes:
                continue
            value = raw[p.id]
            if p is catalog.governance:
                ok = isinstance(value, bool)
            else:
                tokens = _tokens(value)
                labels = [p.option_names.get(t, t) for t in tokens]
                allowed = LABELS | ({'不在地图'} if p is catalog.primary else set())
                ok = bool(tokens) and all(label in allowed for label in labels)
                ok = ok and (isinstance(value, list) if p is catalog.involved else not isinstance(value, list))
            if not ok:
                invalid.append(p.name)
        if invalid:
            add(6, values, evidence=['Invalid fields: ' + ', '.join(invalid)])
        if (valid or hint) and (any(present) or not valid):
            add(7, values, confidence='high' if valid else 'medium', evidence=['Exception candidate has attributes or lacks approved reason'])
        if (entry_raw is not None and not valid) or (hint and not valid) or (outside_value and not valid):
            add(8, entry_raw, confidence='high' if entry else 'medium', evidence=[reason])
        if open_ and '已迁出' in project.get('title', ''):
            add(9, {'project': project.get('title'), 'status': issue.get('status')}, evidence=['New/ongoing work under migrated pointer'])
        elif not open_ and (issue.get('metadata') or {}).get('current_execution') is True:
            add(9, {'status': issue.get('status'), 'current_execution': True}, evidence=['Closed issue explicitly marked current execution'])
    return {'schema_version': 1, 'mode': 'shadow', 'workspace_id': workspace_id, 'detected_at': at,
            'complete': True, 'issue_count': len(issues), 'findings': findings,
            'atlas_decisions': [f for f in findings if f['priority'] == 'high']}


def read_cli(kind, workspace_id, *args, profile=None):
    if kind not in {'issue', 'project', 'property'}:
        raise ValueError('read-only command refused')
    if profile:
        from .cli_runner import CliRunner
        return CliRunner(profile=profile).run([kind, 'list', *args], workspace_id=workspace_id).data
    # Task-scoped credentials remain scoped to the assigned workspace.
    result = subprocess.run(['multica', '--workspace-id', workspace_id, kind, 'list', *args, '--output', 'json'],
                            capture_output=True, text=True, timeout=90)
    if result.returncode:
        raise RuntimeError(f'{kind} list failed (exit {result.returncode})')
    return json.loads(result.stdout)


def collect(workspace_id, profile=None):
    issues, seen, offset = [], set(), 0
    expected_total = None
    while True:
        data = read_cli('issue', workspace_id, '--limit', '100', '--offset', str(offset), '--sort', 'created_at', '--direction', 'asc', profile=profile)
        page = data['issues']
        total = data.get('total')
        if not isinstance(total, int) or total < 0:
            raise ValueError('inventory total unavailable')
        if expected_total is not None and total != expected_total:
            raise ValueError('inventory total changed during pagination')
        expected_total = total
        if data.get('has_more') and not page:
            raise ValueError('incomplete pagination: empty page')
        for row in page:
            if row['id'] in seen:
                raise ValueError('inventory changed during pagination; rerun shadow scan')
            seen.add(row['id'])
            issues.append(row)
        if not data['has_more']:
            if len(issues) != expected_total:
                raise ValueError('incomplete inventory')
            break
        offset += len(page)
    projects = read_cli('project', workspace_id, profile=profile)
    definitions = read_cli('property', workspace_id, profile=profile)
    if not isinstance(projects, list) or not isinstance(definitions, list):
        raise ValueError('unexpected catalog response')
    if any(p.get('workspace_id') != workspace_id for p in projects):
        raise ValueError('project workspace mismatch')
    return issues, projects, definitions


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace-id', required=True)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--exceptions', type=Path)
    parser.add_argument('--profile', help='Atlas read-only CLI profile; required for Atlas ingestion')
    args = parser.parse_args(argv)
    approved = json.loads(args.exceptions.read_text()) if args.exceptions else {}
    report = inspect(*collect(args.workspace_id, args.profile), workspace_id=args.workspace_id, exceptions=approved)
    report['source_profile'] = args.profile
    # Atomic replacement keeps Atlas from reading a partial successful report.
    temporary = args.output.with_suffix(args.output.suffix + '.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    temporary.replace(args.output)
    print(json.dumps({'mode': report['mode'], 'issue_count': report['issue_count'],
                      'findings': len(report['findings']), 'atlas_decisions': len(report['atlas_decisions'])}))
    return 0


def load_for_atlas(db, profile, workspace_id, now=None):
    """Never turn absent, corrupt, stale or mismatched evidence into a healthy scan."""
    try:
        report = json.loads(snapshot_path(db).read_text())
        if (report['schema_version'] != 1 or report['mode'] != 'shadow'
                or report['complete'] is not True or report['workspace_id'] != workspace_id
                or report.get('source_profile') != profile):
            return {'state': 'incompatible', 'findings': []}
        age = ((now or datetime.now(timezone.utc)) - datetime.fromisoformat(report['detected_at'].replace('Z', '+00:00'))).total_seconds()
        if not 0 <= age <= 86400:
            return {'state': 'stale', 'findings': [], 'detected_at': report['detected_at']}
        required = {'issue_id', 'rule', 'code', 'current', 'candidate', 'confidence', 'evidence',
                    'whitelist', 'detected_at', 'source_updated_at', 'fingerprint', 'priority'}
        if not isinstance(report['findings'], list) or any(
                not isinstance(f, dict) or not required <= f.keys()
                or not isinstance(f['evidence'], list) or not isinstance(f['whitelist'], dict)
                or f['rule'] not in {f'P{n}' for n in RULES} for f in report['findings']):
            return {'state': 'invalid', 'findings': []}
        findings = [f for f in report['findings'] if f['priority'] == 'high' and f['source_updated_at']]
        return {'state': 'ok', 'findings': findings, 'detected_at': report['detected_at']}
    except FileNotFoundError:
        return {'state': 'not_collected', 'findings': []}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {'state': 'invalid', 'findings': []}


if __name__ == '__main__':
    raise SystemExit(main())
