# Devin Parent Session Layout Implementation Plan

**Goal:** Implement the approved explicit parent layout while retaining one
session per origin and the consuming agent's queue-only interface.

**Architecture:** A shared pure binding resolver separates main repository and
session workspace. Enrollment and preflight use it; provider keeps request-root
identity but executes in session-root cwd and guards drift before publication.

**Tech Stack:** Python 3.11, unittest/pytest, temporary Git repositories, Devin
CLI, host-owned JSON registries and macOS LaunchAgent.

## 1. Finish the previously authorized wait contract

- [x] Run contract and queue/provider regressions on the existing edits.
- [x] Commit only that contract, scenario, test and its dated spec/plan.

## 2. Repository/session binding

- [x] Add failing tests in `scripts/test_repository_binding.py` for separate
  repository/session roots, implicit exact-root compatibility, unknown layouts,
  strict immediate-parent relation and sibling/symlink boundary rejection.
- [x] Implement pure resolver and target boundary helpers in
  `scripts/repository_binding.py`.
- [x] Keep historical exact-root migration compatible in
  `scripts/migrate_session_registry.py`.
- [x] Resolve `repository_root` explicitly for parent layout and default missing
  legacy v2 roots to `session_root` for exact-root; persist the resolved
  `repository_common_dir` and reject a different common directory even when the
  remote origin matches.

## 3. Host enrollment and preflight

- [x] Add temporary-Git tests in `scripts/test_session_enroll.py` and
  `scripts/test_preflight.py`, and setup-composition tests in
  `scripts/test_host_setup.py`; verify failures before fixing each boundary.
- [x] Let `session_enroll.py` accept explicit `--repo-root` plus optional
  `--worktree-parent`, preserving the old worktree-only exact-root invocation.
- [x] Add explicit layout/check-only arguments; verify root origin/common-dir,
  exact session cwd/model, existing-binding consistency and registry validity.
  Parent layout always requires `--repo-root`; the legacy worktree-only form is
  exact-root only. If `devin list` omits per-session model metadata, rely on the
  exact global `SWE-2 High` catalog entry; reject only an explicit mismatch.
- [x] Pass both roots/layout from `host_setup.py`; validate session eligibility
  before altering allowlist and preserve exact-root sibling-worktree enrollment.
- [x] Preflight parent sessions directly at session cwd, reject drift, separately
  verify execution/target trust, and bind all targets to the enrolled Git
  common-dir. Check origin/common-dir and the DB hint before `devin list`; use
  `session_working_directory_drift` for DB/CLI workspace disagreement.

## 4. Provider and maintainer contract

- [x] Add provider regression tests for cwd, distinct prompt roots, before/after
  session checks and zero publication on drift.
- [x] Implement parent drift guards and protected root/layout/common-dir evidence in
  `scripts/host_provider.py`; keep review JSON schema request-root based.
- [x] Document host-only layout/setup/trust commands in a maintainer reference;
  keep consumer instructions queue-only. Add pressure scenarios and changelog.
- [x] Append only the design transition to the timeline; omit external PR heads
  and session IDs.

## 5. Verify, install and hand off

- [x] Run every `test_*.py` test (including pytest-style functions), compile and
  `git diff --check`; obtain independent skill/spec/implementation review.
- [x] Commit this change separately from unrelated dirty documentation.
- [ ] Use host proxy variables to check/enroll the authorized repository.
- [ ] Check queue/provider activity, restart the canonical login worker when
  idle, verify live preflight and message the authorized destination chat.
- [ ] Wait for its normalized review outcome; preserve failures as evidence and
  do not claim a preflight alone completed review.
