"""Account evidence is synthetic; no provider requests or actual agent writes."""
import copy
import unittest

import test_write_actions as fixtures
from test_write_actions import AGENT, WS, RT, OTHER, TOKEN
from multica_board.model_availability import assess
from multica_board.write_adapter import WriteError


def evidence(status='available', now=100, model='new-model'):
    return dict(workspace_id=WS, runtime_id=RT, model=model, status=status,
                source='account_model_list', observed_at=now,
                complete=True, account_bound=True, context_revision='fixture-account-v1')


class AvailabilityTests(unittest.TestCase):
    setUp = fixtures.ActionTests.setUp
    tearDown = fixtures.ActionTests.tearDown
    preview = fixtures.ActionTests.preview
    execute = fixtures.ActionTests.execute
    assertCode = fixtures.ActionTests.assertCode

    def probe(self, row):
        self.adapter.model_availability = lambda *_: copy.deepcopy(row)

    def test_unknown_available_and_unavailable(self):
        self.assertEqual(self.preview()['availability']['status'], 'unknown')
        for status in ('available', 'unavailable'):
            self.probe(evidence(status))
            p = self.preview()
            self.assertEqual(p['availability']['status'], status)
            self.assertEqual(p['availability']['requires_extra_confirmation'], status == 'unavailable')
            self.assertNotIn('_availability_binding', p)
            self.assertNotIn('fixture-account-v1', str(p) + self.path.read_text())
        self.assertFalse(self.adapter.writes)

    def test_unavailable_needs_this_preview_ack_and_consumes_on_rejection(self):
        self.probe(evidence('unavailable'))
        for ack in (None, False, True, '', OTHER):
            p = self.preview()
            self.assertCode('availability_confirmation_required', lambda:
                            self.actions.execute(p['id'], p['confirmation'], ack))
            self.assertCode('approval_invalid', lambda: self.actions.execute(p['id'], p['confirmation'], p['id']))
        p, other = self.preview(), self.preview()
        self.assertCode('availability_confirmation_required', lambda:
                        self.actions.execute(p['id'], p['confirmation'], other['id']))
        self.assertFalse(self.adapter.writes)
        self.assertEqual(self.actions.execute(other['id'], other['confirmation'], other['id'])['state'], 'applied')
        self.assertEqual(len(self.adapter.writes), 1)

    def test_available_and_unknown_keep_normal_confirmation(self):
        for row in (None, evidence()):
            self.probe(row)
            p = self.preview()
            self.assertEqual(self.execute(p)['state'], 'applied')
            self.adapter.values['model'] = 'old-model'
            self.adapter.version += 1

    def test_changed_account_status_and_loss_of_source_require_new_preview(self):
        for change in (None, evidence('unavailable'), dict(evidence(), context_revision='fixture-account-v2')):
            self.probe(evidence())
            p = self.preview()
            self.probe(change)
            self.assertCode('availability_changed', lambda: self.execute(p))
        self.assertFalse(self.adapter.writes)

    def test_unknown_becoming_unavailable_cannot_bypass_extra_confirmation(self):
        p = self.preview()
        self.probe(evidence('unavailable'))
        self.assertCode('availability_changed', lambda: self.execute(p))
        self.assertFalse(self.adapter.writes)

    def test_old_evidence_bounds_preview_lifetime(self):
        self.now = 390
        self.probe(evidence(now=100))
        p = self.preview()
        self.assertEqual(p['expires_at'], 400)
        self.now = 400
        self.assertCode('approval_expired', lambda: self.execute(p))

    def test_freshness_is_measured_after_live_directory_returns(self):
        def probe(*_):
            self.now += 1
            return evidence(now=self.now)
        self.adapter.model_availability = probe
        p = self.preview()
        self.assertEqual(p['availability']['status'], 'available')
        self.assertEqual(self.execute(p)['state'], 'applied')

    def test_runtime_change_custom_model_and_clear_use_effective_target(self):
        self.probe(evidence())
        self.assertEqual(self.preview({'runtime_id': OTHER})['availability']['status'], 'unknown')
        self.probe(evidence(model='custom'))
        p = self.preview({'custom_model': 'custom'})
        self.assertEqual(p['availability']['status'], 'available')
        for changes in ({'model': None}, {'model': 'auto'}):
            p = self.preview(changes)
            self.assertEqual(p['availability']['reason'], 'automatic_model')

    def test_untrusted_stale_malformed_and_partial_sources_are_unknown(self):
        after = dict(self.adapter.values, model='new-model')
        for patch in ({'account_bound': False}, {'context_revision': ''}, {'workspace_id': OTHER},
                      {'runtime_id': OTHER}, {'model': 'other'}, {'observed_at': -201},
                      {'observed_at': 101}, {'observed_at': float('nan')}, {'observed_at': True},
                      {'complete': False}, {'status': 'unknown'}, {'source': 'generic_catalog'},
                      {'source': 'account_model_denial'}):
            with self.subTest(patch=patch):
                row = dict(evidence(), **patch)
                result, binding = assess(lambda *_: row, (WS, AGENT), after, 100)
                self.assertEqual(result['status'], 'unknown')
                self.assertIsNone(binding)
        def failed(*_):
            raise RuntimeError('fixture-private-provider-output')
        result, _ = assess(failed, (WS, AGENT), after, 100)
        self.assertEqual(result['reason'], 'probe_failed')
        self.assertNotIn('private', str(result))

    def test_client_cannot_inject_availability_or_override_preview(self):
        self.assertCode('invalid_request', lambda: self.actions.handle('preview', {
            'workspace_id': WS, 'agent_id': AGENT, 'changes': {'model': 'new-model'},
            'availability': {'status': 'available'}}, TOKEN))
        p = self.preview()
        p['availability']['status'] = 'unavailable'
        self.assertEqual(self.execute(p)['state'], 'applied')

    def test_rollback_has_fresh_availability_and_separate_ack(self):
        result = self.execute(self.preview())
        self.probe(evidence('unavailable', model='old-model'))
        p = self.actions.rollback(result['id'])
        self.assertEqual(p['availability']['status'], 'unavailable')
        self.assertCode('availability_confirmation_required', lambda: self.execute(p))

    def test_token_off_still_requires_availability_ack(self):
        self.actions.features['write_token_required'] = False
        self.probe(evidence('unavailable'))
        p = self.actions.handle('preview', {'workspace_id': WS, 'agent_id': AGENT,
                                'changes': {'model': 'new-model'}}, None)
        self.assertCode('availability_confirmation_required', lambda: self.actions.handle('execute',
                        {'id': p['id'], 'confirmation': p['confirmation']}, None))

    def test_real_adapter_has_no_credential_or_inference_probe(self):
        from multica_board.write_adapter import MulticaWriteAdapter
        from unittest.mock import Mock
        runner = Mock()
        adapter = MulticaWriteAdapter(runner, {})
        self.assertIsNone(adapter.model_availability((WS, AGENT), self.adapter.values))
        self.assertFalse(runner.mock_calls)
