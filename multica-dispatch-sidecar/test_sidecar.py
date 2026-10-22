import copy
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
            return self.comments
        if args[:2] == ('issue', 'list'):
            return {'issues': self.issues, 'has_more': False}
        if args[:3] == ('issue', 'comment', 'add'):
            self.posts += 1
            body = Path(args[args.index('--content-file') + 1]).read_text()
            if self.mode != 'lost':
                self.comments.append({'content': body})
            if self.mode in ('timeout', 'lost'):
                raise TimeoutError()
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

    def tick(self, now):
        self.write()
        before = self.boxpath.read_bytes()
        try:
            return s.tick(self.root, self.boxpath, self.cli, now)
        finally:
            self.assertEqual(before, self.boxpath.read_bytes())

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
        with self.assertRaises(TimeoutError):
            self.tick(2000)
        state = self.tick(2400)
        self.assertIsNone(state['inflight'])
        self.assertEqual(self.cli.posts, 1)
        self.tick(5000)
        self.assertEqual(self.cli.posts, 1)

    def test_unknown_outcome_fails_closed_even_after_restart(self):
        self.prepare()
        self.cli.mode = 'lost'
        with self.assertRaises(TimeoutError):
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


if __name__ == '__main__':
    unittest.main()
