---
name: devin-pr-review-thread
description: Use when another agent harness must review a GitHub PR through the local Devin CLI, reuse one Devin session per repository, and publish actionable findings as GitHub review threads.
metadata:
  owner: agents-lab
  scope: repo
  version: "0.1.0"
---

# Devin PR Review Thread

This is a narrow adapter between an agent harness, the local `devin` CLI, and GitHub. Devin inspects the repository and returns findings; `gh` is the only writer to the PR.

It is not a review-loop controller. A harness may run this adapter only after the control thread has recorded an explicit bounded review-round decision under [review-round-contract.md](references/review-round-contract.md). A new commit, a fixed finding, or an unresolved thread alone is never an automatic reason to invoke Devin again.

## Contract

- Use exactly the free model whose CLI mapping is `label=SWE-2 High`, `model_uid=swe-2-high`, valid only through **2026-10-26** (local date). Check `devin models list --format json`; accept the UID only when that JSON variant maps it to the exact label. If neither exact mapping is present, stop with `model_unavailable`; never silently substitute another model.
- Each repository has exactly one review session, keyed by canonical `git rev-parse --show-toplevel` path. Record the canonical origin (`owner/name`) for diagnostics.
- Devin's working directory is that repository root, never a PR worktree, subdirectory, temporary clone, or unrelated current directory.
- Store only non-secret metadata in `~/.config/devin/pr-review-sessions.json` (or an explicit equivalent). Never store credentials, tokens, cookies, or untrusted output there.
- Use `--permission-mode auto`; do not grant `accept-edits` or `dangerous`. Devin must not edit, commit, push, or call GitHub. Before non-interactive `--print`, verify the canonical root is trusted; if the CLI reports `workspace_untrusted`, stop with that diagnostic or have the operator trust the exact root. If a read-only tool is denied under `auto`, stop with `permission_denied` and preserve the denial text; inspect permission configuration or organization policy, but never bypass it with `dangerous`.
- Publish only validated findings through `gh api`; never paste unvalidated prose into a PR thread.

## Workflow

1. **Preflight.** Confirm `devin`, `gh`, and `git`; run `gh auth status`; resolve root, origin, PR number, and `headRefOid` with `gh pr view`. Confirm the root is a real Git worktree and not a bare repository. If identity is ambiguous, stop.
2. **Model gate.** Compare the local date with `2026-10-26`; verify the exact `label=SWE-2 High`/`model_uid=swe-2-high` mapping in a variant under `families[].variants[]` using `devin models list --format json`. Missing mapping is `model_unavailable`, not permission to choose another. Pass the verified UID (`--model swe-2-high`) to avoid display-name parsing differences between CLI versions.
3. **Resolve the fixed session under an exclusive lock.** Read the registry entry for the canonical root. If present, verify `devin list --format json` maps that id to the same root. If `session_locked` is returned, inspect only that session's lock file and PID; remove the lock only after `kill -0 <pid>` proves the holder no longer exists. If the holder is alive, stop and ask for its client to close. If absent, create one in the root with the exact model, discover the new id, and atomically write the entry. Never create a second session because a call is slow or the PR changed.
4. **Request structured review.** Resume the fixed session and pass PR number, base/head SHAs, and changed-file scope. Require the JSON contract in [protocol.md](references/protocol.md), no edits/commits/pushes/GitHub writes, and no Markdown wrapper. Use `devin -r <SESSION_ID> --model swe-2-high --permission-mode auto --respect-workspace-trust true -p -- <PROMPT>` after the exact root trust check; adapt only flag order based on installed `devin --help`. If `--respect-workspace-trust` is unsupported, use the CLI's documented equivalent, never `dangerous`.
5. **Validate before publishing.** Keep the response outside the repository or in a mode-0600 temporary file. Derive a mode-0600 JSON map of changed paths to added RIGHT line numbers from the exact PR diff, then run `scripts/validate_review.py` with `--session-id`, `--pr-number`, and `--changed-lines`. Reject malformed JSON, missing identity, missing path/line/side, out-of-diff locations, duplicates, secrets, or executable shell text.
6. **Publish with `gh` only.** For every validated finding, call the pull-request review-comment endpoint through `gh api`, bound to owner/name, PR number, head SHA, relative path, `line`, and `side=RIGHT`. Add a stable marker with session id, head SHA, and finding id; query existing comments first so retries for the same head do not duplicate while a later review head can reuse finding ids. Do not create an empty “LGTM” thread.
7. **Report evidence.** Return root, PR URL/number, head SHA, fixed session id, model, expiry check, finding count, comment URLs/ids, and skipped/failed findings. A successful Devin process is not proof that GitHub accepted a comment.
8. **Hand off to round control.** Record the completed run with `scripts/validate_review_round.py`. The control thread classifies findings, obtains any required product decision, batches approved fixes, validates them, and selects exactly one next action. This adapter must not fix findings, resolve threads, request another review, or infer that a re-review is wanted.

## Registry

Use one JSON object per canonical root, for example:

```json
{
  "/Users/me/src/repo": {
    "repository": "owner/name",
    "session_id": "<devin-session-id>",
    "model": "SWE-2 High",
    "created_at": "2026-09-18T00:00:00Z",
    "last_verified_at": "2026-09-18T00:00:00Z"
  }
}
```

Acquire an exclusive lock while reading, creating, or updating. If a session is missing, verify the session list and recover only that same entry; do not silently rotate it. Rotation requires explicit authorization and an archived reason.

## Failure policy

Fail closed on expiry/unavailability, missing auth, root/session mismatch, dirty identity resolution, malformed review JSON, out-of-diff locations, or unsuccessful `gh api`. Preserve raw output only in a protected temporary file and report its path. Do not blindly retry a failed GitHub write unless marker lookup proves it was not accepted. A stale session lock is recoverable only through the exact PID check in step 3; never delete a lock whose owner is live or unknown.

Do not use `gh pr review --approve` or `--request-changes`: this skill creates review threads, not a review verdict.

Read [references/protocol.md](references/protocol.md) when constructing or interpreting the Devin response, and [references/review-round-contract.md](references/review-round-contract.md) before starting or continuing a review loop. Run `scripts/validate_review.py --help` before publishing and `scripts/validate_review_round.py --help` before recording the round.
