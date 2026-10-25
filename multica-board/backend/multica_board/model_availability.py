"""Account-scoped read-only evidence boundary. Never infer access from a catalog.

Only server-side adapters may supply evidence; HTTP bodies cannot supply it.
No current Multica runtime endpoint establishes provider account binding, so
the production adapter deliberately returns unknown. A future provider bridge
must implement this contract without reading credentials or starting a turn.
"""
import math

from .write_adapter import fingerprint

MAX_AGE = 300
SOURCES = {'account_model_list', 'account_model_denial'}


def assess(probe, target, after, now):
    model = after.get('custom_model') or after.get('model')
    unknown = {'status': 'unknown', 'source': 'none',
               'reason': 'account_source_unavailable', 'observed_at': None,
               'requires_extra_confirmation': False}
    if not model or model.lower() == 'auto':
        return dict(unknown, reason='automatic_model'), None
    try:
        evidence = probe(target, after) if probe else None
        # Measure freshness after the read, so a freshly returned observation
        # is not mistaken for future evidence because the request took time.
        now = now() if callable(now) else now
        if not isinstance(evidence, dict):
            return unknown, None
        # The source must establish the runtime AND its active account context.
        # This context stays private; it must be an opaque non-secret revision,
        # never a credential, credential hash, email, or full session log.
        if (evidence.get('workspace_id') != target[0]
                or evidence.get('runtime_id') != after['runtime_id']
                or evidence.get('model') != model
                or evidence.get('account_bound') is not True
                or not isinstance(evidence.get('context_revision'), str)
                or not evidence['context_revision']):
            return dict(unknown, reason='account_binding_unknown'), None
        at = evidence.get('observed_at')
        if (type(at) not in (int, float) or not math.isfinite(at)
                or not 0 <= now - at < MAX_AGE):
            return dict(unknown, reason='evidence_expired'), None
        status, source = evidence.get('status'), evidence.get('source')
        if (status not in ('available', 'unavailable') or source not in SOURCES
                or (status == 'available' and source != 'account_model_list')
                or evidence.get('complete') is not True):
            return dict(unknown, reason='evidence_incomplete'), None
        public = {'status': status, 'source': source,
                  'reason': 'listed_for_account' if status == 'available' else 'denied_for_account',
                  'observed_at': at, 'requires_extra_confirmation': status == 'unavailable'}
        binding = fingerprint({k: evidence[k] for k in
                               ('workspace_id', 'runtime_id', 'model', 'context_revision', 'status', 'source')})
        return public, binding
    except Exception:
        # Never expose provider output, exception messages, or stderr.
        return dict(unknown, reason='probe_failed'), None
