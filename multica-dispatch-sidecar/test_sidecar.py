import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
import sidecar as s


def event(eid, issue='source', kind='status', **extra):
    p = dict(workspace_id=s.WS, issue_id=issue, kind=kind, new_status='in_review',
             identifier='ACM-999', event_id=eid, **extra)
    return {'payload': p, 'created_at': '1970-01-01T00:20:00Z'}


class FakeCLI:
    def __init__(self):
        self.comments = []
        self.roots = None
        self.since = None
        self.posts = 0
        self.active = []
        self.mode = 'ok'
        self.issues = []
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)
        if args[:2] == ('issue', 'get'):
            return {'assignee_id': s.AGENT, 'status': 'todo'}
        if args[:2] == ('issue', 'runs'):
            return self.active
        if args[:3] == ('issue', 'comment', 'list'):
            if '--since' in args:
                return self.since if self.since is not None else self.comments
            return self.roots if self.roots is not None else self.comments
        if args[:2] == ('issue', 'list'):
            return {'issues': self.issues, 'has_more': False}
        if args[:3] == ('issue', 'comment', 'add'):
            self.posts += 1
            body = Path(args[args.index('--content-file') + 1]).read_text()
            if self.mode == 'rejected':
                raise s.CLIError(1, 'rejected', 'HTTP 403 forbidden')
            if self.mode == 'leak':
                raise s.CLIError(1, 'unknown',
                                 'token=mat_' + 'k' * 40 + ' Bearer ' + 'b' * 40 +
                                 ' admin@example.com')
            if self.mode != 'lost':
                self.comments.append({'content': body})
            if self.mode in ('timeout', 'lost'):
                raise s.CLIError(None, 'unknown', 'timeout after 90s')
            return {'id': 'comment'}
        raise AssertionError(args)


class SidecarTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'sidecar'
        self.boxpath = Path(self.temp.name) / 'outbox.json'
        self.box = {'items': {'old': event('old')}}
        self.write()
        self.cli = FakeCLI()

    def write(self):
        self.boxpath.write_text(json.dumps(self.box))

    def state(self):
        return s.read(self.root / 'state.json')

    def tick(self, now):
        self.write()
        before = self.boxpath.read_bytes()
        try:
            return s.tick(self.root, self.boxpath, self.cli, now)
        finally:
            self.assertEqual(before, self.boxpath.read_bytes())

    def logged(self, now):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            state = self.tick(now)
        return state, out.getvalue()

    def prepare(self):
        self.tick(1000)
        self.box['items']['a'] = event('a')

    def test_baseline_no_history_or_cli(self):
        state = self.tick(1000)
        self.assertEqual(state['pending'], {})
        self.assertEqual(self.cli.calls, [])

    def test_merge_signal_types_and_never_touch_original(self):
        self.prepare()
        self.box['items']['b'] = event('b', kind='agent_delivery', comment_id='c')
        state = self.tick(2000)
        self.assertEqual(self.cli.posts, 1)
        body = self.cli.comments[0]['content']
        self.assertEqual(body.count('mention://issue/'), 1)
        self.assertIn('agent_delivery / status', body)
        self.assertEqual(state['pending'], {})
        self.tick(4000)
        self.assertEqual(self.cli.posts, 1)

    def test_active_run_holds_then_releases(self):
        self.prepare()
        self.cli.active = [{'id': 'run'}]
        self.assertEqual(len(self.tick(2000)['pending']), 1)
        self.assertEqual(self.cli.posts, 0)
        self.cli.active = []
        self.tick(2300)
        self.assertEqual(self.cli.posts, 1)

    def test_timeout_after_server_accept_reconciles_without_duplicate(self):
        self.prepare()
        self.cli.mode = 'timeout'
        with self.assertRaises(s.CLIError):
            self.tick(2000)
        state = self.tick(2400)
        self.assertIsNone(state['inflight'])
        self.assertEqual(self.cli.posts, 1)
        self.tick(5000)
        self.assertEqual(self.cli.posts, 1)

    def test_marker_visible_only_via_since_scan_reconciles(self):
        self.prepare()
        self.cli.mode = 'timeout'
        with self.assertRaises(s.CLIError):
            self.tick(2000)
        self.cli.roots = []
        state = self.tick(2400)
        self.assertIsNone(state['inflight'])
        self.assertEqual(self.cli.posts, 1)
        self.assertTrue(any('--since' in call for call in self.cli.calls))

    def test_unknown_outcome_fails_closed_even_after_restart(self):
        self.prepare()
        self.cli.mode = 'lost'
        with self.assertRaises(s.CLIError):
            self.tick(2000)
        self.assertIsNotNone(self.tick(2400)['inflight'])
        self.tick(6000)
        self.assertEqual(self.cli.posts, 1)

    def test_rate_limit_and_twenty_issue_cap(self):
        self.prepare()
        for i in range(25):
            self.box['items'][str(i)] = event(str(i), issue='issue' + str(i))
        state = self.tick(2000)
        self.assertEqual(len(state['pending']), 6)
        self.tick(2600)
        self.assertEqual(self.cli.posts, 1)
        self.assertEqual(self.tick(2900)['pending'], {})
        self.assertEqual(self.cli.posts, 2)

    def test_self_inbox_and_other_workspace_excluded(self):
        self.tick(1000)
        self.box['items']['self'] = event('self', assignee_id=s.AGENT)
        self.box['items']['selfrun'] = event('selfrun', kind='run_failed', agent='副调度')
        self.box['items']['inbox'] = event('inbox', issue=s.INBOX)
        foreign = event('foreign')
        foreign['payload']['workspace_id'] = 'eo'
        self.box['items']['foreign'] = foreign
        self.assertEqual(self.tick(2000)['pending'], {})
        self.assertEqual(self.cli.posts, 0)

    def test_gap_resync_current_state(self):
        self.tick(1000)
        self.cli.issues = [{'id': 'missed', 'status': 'blocked', 'updated_at': 'now',
                            'identifier': 'ACM-998', 'assignee_id': 'worker'}]
        state = self.tick(90000)
        self.assertEqual(self.cli.posts, 1)
        self.assertFalse(state['resync_due'])
        self.assertIn('断档', self.cli.comments[0]['content'])

    def test_source_payload_revision_not_dropped(self):
        self.prepare()
        self.tick(2000)
        self.box['items']['a']['payload']['new_status'] = 'blocked'
        self.tick(3000)
        self.assertEqual(self.cli.posts, 2)
        self.assertIn('blocked', self.cli.comments[-1]['content'])

    def test_source_mentions_not_forwarded(self):
        self.prepare()
        self.box['items']['a']['payload']['title'] = '[@bad](mention://agent/other)'
        self.tick(2000)
        self.assertNotIn('mention://agent/other', self.cli.comments[0]['content'])

    def test_invalid_source_does_not_replace_saved_state(self):
        self.tick(1000)
        before = (self.root / 'state.json').read_bytes()
        self.boxpath.write_text('{')
        with self.assertRaises(json.JSONDecodeError):
            s.tick(self.root, self.boxpath, self.cli, 2000)
        self.assertEqual(before, (self.root / 'state.json').read_bytes())

    def test_rejection_auto_releases_then_redelivers_once(self):
        self.prepare()
        self.cli.mode = 'rejected'
        out = io.StringIO()
        with self.assertRaises(s.CLIError), contextlib.redirect_stdout(out):
            self.tick(2000)
        state = self.state()
        self.assertIsNone(state['inflight'])
        self.assertEqual(len(state['pending']), 1)
        self.assertEqual(self.cli.posts, 1)
        self.assertTrue(any(r['action'] == 'auto_release_rejected'
                            for r in state['audit']))
        self.assertIn('category=rejected', out.getvalue())
        self.assertEqual(state['last_error']['category'], 'rejected')
        self.cli.mode = 'ok'
        state = self.tick(3000)
        self.assertEqual(state['pending'], {})
        self.assertEqual(self.cli.posts, 2)
        self.tick(6000)
        self.assertEqual(self.cli.posts, 2)

    def test_unresolved_inflight_holds_and_stall_alert_throttles(self):
        self.prepare()
        self.cli.mode = 'lost'
        with self.assertRaises(s.CLIError):
            self.tick(2000)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.tick(10000)
            self.tick(24000)
            self.tick(25800)
            self.tick(27700)
        lines = out.getvalue()
        self.assertEqual(self.cli.posts, 1)
        self.assertIsNotNone(self.state()['inflight'])
        stalled = [line for line in lines.splitlines() if 'stalled' in line]
        self.assertEqual(len(stalled), 2)
        self.assertIn('inflight=True', stalled[0])
        self.assertNotIn('stalled', lines.splitlines()[0])

    def test_adjudicate_verdicts(self):
        self.prepare()
        self.cli.mode = 'lost'
        with self.assertRaises(s.CLIError):
            self.tick(2000)
        bid = self.state()['inflight']['id']
        before = (self.root / 'state.json').read_bytes()
        s.command(['adjudicate', '--batch', bid, '--verdict', 'not-delivered',
                   '--operator', 'op', '--evidence', 'checked', '--root', str(self.root)])
        self.assertEqual(before, (self.root / 'state.json').read_bytes())
        with self.assertRaises(SystemExit):
            s.command(['adjudicate', '--batch', 'wrong', '--verdict', 'abandon',
                       '--operator', 'op', '--evidence', 'x',
                       '--root', str(self.root), '--apply'])
        with self.assertRaises(SystemExit):
            s.command(['adjudicate', '--batch', bid, '--verdict', 'delivered',
                       '--operator', 'op', '--evidence', 'x',
                       '--root', str(self.root), '--apply'])
        s.command(['adjudicate', '--batch', bid, '--verdict', 'not-delivered',
                   '--operator', 'op', '--evidence', 'server shows no comment',
                   '--root', str(self.root), '--apply'])
        state = self.state()
        self.assertIsNone(state['inflight'])
        self.assertEqual(len(state['pending']), 1)
        self.assertEqual(state['retry_at'], 0)
        self.assertEqual(len(list(self.root.glob('state.json.bak-*'))), 1)
        self.assertTrue((self.root / 'audit.jsonl').exists())
        self.cli.mode = 'ok'
        self.assertEqual(self.tick(self.state()['last_attempt'] + 1000)['pending'], {})
        self.assertEqual(self.cli.posts, 2)

        self.box['items']['b'] = event('b')
        self.cli.mode = 'lost'
        with self.assertRaises(s.CLIError):
            self.tick(self.state()['last_attempt'] + 1000)
        bid = self.state()['inflight']['id']
        s.command(['adjudicate', '--batch', bid, '--verdict', 'abandon',
                   '--operator', 'op', '--evidence', 'events superseded',
                   '--root', str(self.root), '--apply'])
        state = self.state()
        self.assertIsNone(state['inflight'])
        self.assertIn('b', state['retired'])
        self.assertNotIn('b', state['pending'])
        self.assertTrue(state['retired']['b']['reason'].startswith('abandon:'))

        self.box['items']['c'] = event('c')
        with self.assertRaises(s.CLIError):
            self.tick(self.state()['last_attempt'] + 1000)
        bid = self.state()['inflight']['id']
        s.command(['adjudicate', '--batch', bid, '--verdict', 'delivered',
                   '--operator', 'op', '--evidence', 'found on server',
                   '--comment-id', 'cmt-1', '--root', str(self.root), '--apply'])
        state = self.state()
        self.assertIsNone(state['inflight'])
        self.assertEqual(state['delivered'][-1]['batch_id'], bid)
        self.assertEqual(state['audit'][-1]['comment_id'], 'cmt-1')

    def test_retire_and_rollback(self):
        self.prepare()
        self.box['items']['b'] = event('b')
        self.cli.active = [{'id': 'run'}]
        self.tick(2000)
        self.assertEqual(len(self.state()['pending']), 2)
        ledger = {'id': 'L1', 'operator': 'op', 'entries': [
            {'issue_id': 'source', 'identifier': 'ACM-999',
             'event_ids': ['a', 'b', 'gone'], 'reason': 'superseded',
             'evidence': 'manual review',
             'fingerprints': {'a': s.fingerprint(self.state()['pending']['a']),
                              'b': '0' * 64, 'gone': '0' * 64}}]}
        ledgerpath = Path(self.temp.name) / 'ledger.json'
        ledgerpath.write_text(json.dumps(ledger))
        before = (self.root / 'state.json').read_bytes()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            s.command(['retire', '--ledger', str(ledgerpath), '--root', str(self.root)])
        self.assertIn('retired=1 missing=1 changed=1', out.getvalue())
        self.assertEqual(before, (self.root / 'state.json').read_bytes())
        with contextlib.redirect_stdout(out := io.StringIO()):
            s.command(['retire', '--ledger', str(ledgerpath),
                       '--root', str(self.root), '--apply'])
        state = self.state()
        self.assertIn('a', state['retired'])
        self.assertNotIn('a', state['pending'])
        self.assertIn('b', state['pending'])
        self.assertEqual(state['ledgers_applied'], ['L1'])
        with self.assertRaises(SystemExit):
            s.command(['retire', '--ledger', str(ledgerpath),
                       '--root', str(self.root), '--apply'])
        self.tick(3000)
        self.assertNotIn('a', self.state()['pending'])
        s.command(['rollback', '--ledger-id', 'L1',
                   '--root', str(self.root), '--apply'])
        state = self.state()
        self.assertIn('a', state['pending'])
        self.assertNotIn('a', state['retired'])
        self.assertEqual(state['ledgers_applied'], [])
        self.assertIsNone(state['inflight'])

    def test_error_summary_is_redacted(self):
        self.prepare()
        self.cli.mode = 'leak'
        out = io.StringIO()
        with self.assertRaises(s.CLIError), contextlib.redirect_stdout(out):
            self.tick(2000)
        text = out.getvalue() + json.dumps(self.state()['last_error'])
        self.assertNotIn('mat_', text)
        self.assertNotIn('Bearer', text)
        self.assertNotIn('admin@example.com', text)
        self.assertNotIn('k' * 40, text)
        self.assertIn('category=unknown', text)
        raw = 'token=mat_abc Authorization: Bearer ' + 'x' * 40 + ' a@b.co ' + 'y' * 40
        clean = s.redact(raw)
        self.assertNotIn('mat_abc', clean)
        self.assertNotIn('x' * 40, clean)
        self.assertLessEqual(len(clean), 200)

    def test_unchanged_outbox_never_repends_changed_does(self):
        self.prepare()
        self.tick(2000)
        self.tick(5000)
        self.assertEqual(self.cli.posts, 1)
        self.assertEqual(self.state()['pending'], {})
        self.box['items']['a']['payload']['new_status'] = 'blocked'
        self.tick(8000)
        self.assertEqual(self.cli.posts, 2)


if __name__ == '__main__':
    unittest.main()
