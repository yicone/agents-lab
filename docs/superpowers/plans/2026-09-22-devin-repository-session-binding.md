# Devin Repository Session Binding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Reuse one fixed Devin session for a GitHub repository across its main workspace and nested worktrees while preserving origin and trust boundaries.

**Architecture:** Add shared origin normalization and repository-level registry resolution. Preflight verifies the fixed session against the registered main workspace, while provider execution targets the validated worktree. Add a host-only session enrollment command and regression tests; keep `host_enroll.py` limited to GitHub allowlist enrollment.

**Tech Stack:** Python 3.11, JSON registries, Devin CLI, existing provider contract tests.

---

### Task 1: Add origin and registry helpers

**Files:**
- Create: `skills/devin-pr-review-thread/scripts/repository_binding.py`
- Test: `skills/devin-pr-review-thread/scripts/test_repository_binding.py`

- [ ] Write tests for SSH/HTTPS normalization, `.git` stripping, ambiguous remotes, exact origin lookup, duplicate session references, and worktree boundary checks.
- [ ] Cover rejection of credentials, ports, enterprise hosts, query strings, fragments, and malformed/multiple remotes; preserve owner/repository case.
- [ ] Run the focused test file and verify the new tests fail.
- [ ] Implement pure helpers with no Devin or GitHub writes.
- [ ] Run the focused tests and verify they pass.

### Task 2: Make preflight resolve repository-level sessions

**Files:**
- Modify: `skills/devin-pr-review-thread/scripts/preflight.py`
- Test: `skills/devin-pr-review-thread/scripts/test_repository_binding.py`

- [ ] Change registry lookup from exact root to normalized origin v2.
- [ ] Detect legacy root-keyed or mixed registries as read-only input; refuse review until host-only enrollment migrates them atomically to v2.
- [ ] Keep session root verification against the registered main workspace.
- [ ] Reject worktrees with different origin or outside the enrolled boundary.
- [ ] Preserve stable `session_*` and `workspace_untrusted` statuses.
- [ ] Run preflight-related tests and compile checks.

### Task 3: Add host-only session enrollment

**Files:**
- Create: `skills/devin-pr-review-thread/scripts/session_enroll.py`
- Modify: `skills/devin-pr-review-thread/SKILL.md`
- Test: `skills/devin-pr-review-thread/scripts/test_repository_binding.py`

- [ ] Add a command that verifies/creates exactly one fixed session for a repository origin under an exclusive lock.
- [ ] Migrate a validated legacy root entry to v2 atomically and refuse ambiguous or mixed-version input.
- [ ] Write the v2 registry atomically with mode `0600`.
- [ ] Fail closed on uncertain creation, mismatched root, model mismatch, or duplicate session references.
- [ ] Document that this is host setup, distinct from allowlist enrollment and worker startup.

### Task 4: Wire provider target validation and worker guidance

**Files:**
- Modify: `skills/devin-pr-review-thread/scripts/host_provider.py`
- Modify: `skills/devin-pr-review-thread/scripts/host_worker.py`
- Modify: `skills/devin-pr-review-thread/SKILL.md`

- [ ] Pass the validated worktree target to Devin while retaining the registered main workspace as session trust anchor.
- [ ] Invoke Devin with `cwd=<validated worktree root>` and include the same absolute target in the review prompt; reject targets outside the registered root/worktree boundary.
- [ ] Ensure authorization/preflight failures return stable `await-user` or `provider-unavailable`, not misleading `invalid-request` caused by missing host authorization.
- [ ] State that worker is a host-side long-running process and does not create sessions.

### Task 5: Regression and acceptance checks

**Files:**
- Modify: `skills/devin-pr-review-thread/scripts/test_host_provider.py`

- [ ] Add nested worktree, cross-origin, missing registry, and worker authorization tests.
- [ ] Assert missing/inconsistent allowlist returns `await-user` with evidence, while cross-origin/out-of-bound targets return `invalid-request`.
- [ ] Add a worktree-outside-session-root-parent fixture and verify it is rejected.
- [ ] Run all skill tests, `py_compile`, and a temporary registry/allowlist integration check.
- [ ] Run the exact preflight fixture for a main root plus nested worktree.
- [ ] Review the diff and preserve unrelated dirty files.
