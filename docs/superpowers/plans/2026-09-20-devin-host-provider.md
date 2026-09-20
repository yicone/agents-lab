# Devin Host Provider Implementation Plan

> **For agentic workers:** Execute this plan task-by-task with the existing provider and review validators.

**Goal:** Add a host-side JSON command that safely brokers bounded Devin PR reviews for sandbox callers.

**Architecture:** A Python wrapper validates a host-issued authorization ledger, repository allowlist, and request schema, then serializes one request per canonical root. It delegates provider execution only after preflight and returns sanitized JSON with an opaque evidence reference; raw diagnostics remain host-owned.

**Tech Stack:** Python 3 standard library, existing `preflight.py`, `validate_review.py`, `validate_review_round.py`, GitHub CLI, Devin CLI.

---

### Task 1: Implement request/response boundary

**Files:**
- Create: `skills/devin-pr-review-thread/scripts/host_provider.py`
- Create: `skills/devin-pr-review-thread/scripts/test_host_provider.py`

- [ ] Validate schema, absolute canonical root, PR number, timeout, and unknown fields.
- [ ] Validate host-issued authorization and allowlist binding.
- [ ] Emit exactly one sanitized response object; keep evidence opaque.
- [ ] Test malformed, stale, cross-repository, and valid requests.

### Task 2: Add durable concurrency and idempotency

**Files:**
- Modify: `skills/devin-pr-review-thread/scripts/host_provider.py`
- Modify: `skills/devin-pr-review-thread/scripts/test_host_provider.py`

- [ ] Add atomic ledger states and cross-process root lock.
- [ ] Reconcile completed authorization IDs before rerunning.
- [ ] Test busy root, duplicate authorization, and crash-state recovery.

### Task 3: Validate and document

**Files:**
- Modify: `skills/devin-pr-review-thread/SKILL.md`
- Modify: `skills/devin-pr-review-thread/references/runtime-recovery.md`

- [ ] Document the host command as the only sandbox entrypoint.
- [ ] Run contract tests, preflight, existing validators, and quick validation.
- [ ] Preserve unrelated worktree changes.
