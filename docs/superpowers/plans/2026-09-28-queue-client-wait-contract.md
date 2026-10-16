# Queue Client Wait Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent consuming agents from declaring a Devin review failed while the original queue client process is still running.

**Architecture:** Preserve the one-shot queue client and single final JSON response. Encode the pending-process boundary in the skill contract, pressure scenarios, and a documentation regression test.

**Tech Stack:** Markdown skill contract, Python regression test, existing queue client tests.

---

### Task 1: Add the failing contract regression

**Files:**
- Create: `skills/devin-pr-review-thread/scripts/test_skill_contract.py`

- [x] Add assertions that `SKILL.md` requires waiting on the same running process and forbids interpreting absent intermediate stdout as provider failure.
- [x] Run `python3 skills/devin-pr-review-thread/scripts/test_skill_contract.py` and verify it fails before the contract text exists.

### Task 2: Strengthen the consuming-agent contract

**Files:**
- Modify: `skills/devin-pr-review-thread/SKILL.md`
- Modify: `skills/devin-pr-review-thread/pressure-scenarios.md`

- [x] Add the minimal wait rule beside the queue-client invocation contract.
- [x] Add a pressure scenario covering a running queue client, a failed terminal-inspection attempt, and a delayed successful response.
- [x] Keep retry, timeout, JSON schema, and provider ownership rules unchanged.

### Task 3: Verify and commit

**Files:**
- Test: `skills/devin-pr-review-thread/scripts/test_skill_contract.py`
- Test: `skills/devin-pr-review-thread/scripts/test_queue_client.py`
- Test: `skills/devin-pr-review-thread/scripts/test_host_provider.py`

- [x] Run the new regression test and existing queue/provider tests.
- [x] Run `git diff --check`.
- [x] Review the final diff for accidental protocol or timeout changes.
- [x] Commit the skill-contract change as one focused commit.
