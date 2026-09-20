# Devin PR Review Runtime Hardening Design

## Goal

Make `devin-pr-review-thread` deterministic across agent harnesses by replacing ad-hoc CLI interpretation with a machine-readable preflight and an explicit, bounded recovery state machine. Preserve the existing fixed-session, exact-model, GitHub-writer, and finite-review-round invariants.

## Scope

The change adds a read-only preflight helper, documents runtime failure classes, and extends pressure coverage. The consuming-agent interface is intentionally black-box: it receives a stable status and protected evidence path, while Devin/ACP, model, trust, lock, Desktop, and permission details remain provider-owned. It does not publish reviews, trust workspaces, terminate processes, delete locks, open or close Devin Desktop, rotate sessions, change permission mode, or control review rounds.

## Components

1. `scripts/preflight.py` resolves and reports the canonical repository identity, exact `SWE-2 High` model mapping, workspace trust, fixed session binding, session-lock ownership, relevant Devin/ACP processes, required CLI flags, GitHub authentication, and PR identity. Its output is one JSON object with a stable status and evidence fields.
2. `references/runtime-recovery.md` defines failure classification, whether human action is required, and the only permitted retry for startup failures. Devin Desktop is explicitly not a prerequisite; a possible live owner is evidence to investigate, not proof of conflict.
3. `SKILL.md` exposes only the provider preflight and stable result contract to consuming agents; runtime recovery remains a maintainer reference.
4. Pressure scenarios exercise model parsing, trusted and untrusted roots, live and stale locks, missing registry entries, ACP/tool permission separation, and process evidence without changing real user configuration.

## Preflight Interface

Invoke the helper as:

```text
preflight.py --repo-root PATH --pr NUMBER [--registry PATH] [--trusted-workspaces PATH] [--session-lock-dir PATH]
```

It writes exactly one `devin-pr-review/preflight-v1` JSON object to stdout and human diagnostics to stderr. Exit `0` means `status=ok`; exit `2` means a known closed state; exit `3` means an input or dependency returned malformed, mixed, timed-out, or otherwise unverifiable evidence; exit `4` means an internal helper error. Output and diagnostics redact tokens, credentials, cookies, environment values, prompt content, and raw model output.

Mandatory fields are `schema`, `status`, `checked_at`, `repository` (`canonical_root`, `origin`), `pr` (`number`, `head_sha`), `model` (`label`, `model_uid`, `expires_on`, `available`), `workspace_trust` (`state`, `source`), `session` (`id`, `registry_state`, `list_state`, `root`), `lock` (`state`, `path`, `pid`, `holder_identity`), `runtime` (`desktop_or_acp_processes`), `capabilities`, and `diagnostics`. Unknown or unverified evidence is represented explicitly and never coerced to a safe value.

All keys above are always present. `schema`, `status`, `checked_at`, model identity/expiry, and each `state` field are strings. `repository.canonical_root`, `repository.origin`, `pr.head_sha`, `session.id`, `session.root`, `lock.path`, and `lock.holder_identity` are strings or JSON `null`; `pr.number` and `lock.pid` are integers or `null`; `model.available` is boolean or `null`; `runtime.desktop_or_acp_processes`, `capabilities`, and `diagnostics` are arrays of objects/strings. Missing evidence is `null`, never an empty string or an omitted key. State enums explain why evidence is null.

Every subprocess has a finite timeout. `devin models list --format json` and `devin list --format json` must be a single valid JSON document; timeout, non-zero status, malformed JSON, or mixed prose is classified as `model_output_invalid` or `session_output_invalid`. `devin --help` is inspected only for required flag presence. `gh auth status` and `gh pr view --json ...` failures are `github_auth_failed` and `pr_identity_failed` respectively.

The canonical root is `realpath(git rev-parse --show-toplevel)`; registry, trust, session root, and invocation cwd comparisons use that same normalized value. A linked worktree therefore remains a distinct root and cannot silently reuse the main-workspace session.

Trust is read from the installed CLI's documented trust metadata when available. Missing metadata, unsupported format, or an unrecognized CLI version is `workspace_trust_unknown`, not `workspace_untrusted`. The helper never writes the trust store.

Lock inspection reads only the fixed session's lock. `kill -0` is insufficient to call it stale or live: holder identity must also match available lock metadata and process command/start evidence. A missing PID is `session_lock_stale`; a matching live holder is `session_locked_live`; PID reuse, incomplete metadata, races, or conflicting evidence is `session_lock_ambiguous`. The helper re-reads lock identity after process inspection so a changed lock is ambiguous. It never removes a lock.

## Recovery State Machine

| Status | Permitted next action | Retryable in this provider invocation? | Terminal record |
| --- | --- | --- | --- |
| `ok` | Start/resume the fixed session | n/a | continue |
| `model_unavailable`, `model_output_invalid`, `model_expired` | Correct availability/tooling outside the run | No | `await-user` |
| `workspace_untrusted`, `workspace_trust_unknown` | Operator trusts or verifies the exact canonical root, then start a new explicitly authorized attempt | No | `await-user` |
| `session_registry_missing` | Under the registry creation lock, create exactly one session and atomically register it | Not a retry | continue or `await-user` |
| `session_missing` | Recover the same registered identity if provable; never rotate automatically | No | `await-user` |
| `session_registration_unknown` | Reconcile the one possibly created session under the registry lock; never create another | No | `await-user` |
| `session_root_mismatch`, `session_output_invalid` | Diagnose registry/session metadata | No | `await-user` |
| `session_locked_live`, `session_lock_ambiguous` | Ask the holder/operator to release or diagnose it | No | `await-user` |
| `session_lock_stale` | A caller may remove only this exact unchanged lock after a fresh identity/PID check | Not a retry | rerun preflight |
| `github_auth_failed`, `pr_identity_failed` | Repair authentication or resolve PR identity | No | `await-user` |
| `permission_denied` | Preserve denial evidence; inspect policy outside the run | No | `await-user` |
| `invalid_review_output` | Preserve protected raw output | No | `await-user` |
| `acp_startup_failed` | Diagnose holder/process/lock state, run fresh preflight, then retry the identical invocation once only if status is `ok` | Once | second failure: `await-user` |

`acp_startup_failed` means the CLI failed before a model response or tool decision was established and emitted an ACP/session-start transport error. Permission denial, model/tool output failure, malformed review JSON, workspace trust, and live/ambiguous locks never enter this state.

The retry counter is scoped to one recorded provider `run` decision and stored in its protected run evidence as `transport_attempt` (`1` initially, `2` for the sole retry). It survives harness restarts. Before attempt 2 the harness must run a fresh preflight and prove the canonical root, session id, model UID, permission mode, PR head, and invocation arguments are unchanged. A transport retry neither consumes nor creates a review round.

First-session creation is serialized by the registry's exclusive lock: re-read the registry and `devin list` after acquiring it, create only if still absent, and atomically write the entry. If creation succeeds but registration cannot be proven, stop with `session_registration_unknown`; never create again. Registry/session root mismatches and registered-but-missing sessions always stop without rotation.

## State and Safety Model

The helper is observational. It reads the exact canonical root, local Devin metadata, CLI output, and GitHub metadata, but never repairs state. A non-interactive harness returns `workspace_untrusted` and waits for an operator to trust only the exact root. A live or ambiguous lock is never removed. A stale lock may be reported as recoverable but removal remains a separate, deliberate action under the existing PID check.

`acp_startup_failed` permits at most one retry under the state-machine gate above. A second failure stops at `await-user`.

## Desktop and Permission Semantics

Devin Desktop need not be open for CLI review and the skill must not open it. If startup fails and Desktop or another Devin/ACP client appears to hold the fixed session, the harness asks the operator to release it. Desktop being open alone is not enough to kill a process or delete a lock.

`permission_denied` never authorizes `accept-edits`, `dangerous`, a replacement model, or a second session. Supplying a host-verified diff as a future fallback remains out of scope until a separate protocol defines provenance, head binding, format, size, and validation.

## Validation

- Preflight runs read-only against the current CLI and emits the documented JSON contract; malformed and unavailable dependencies fail closed.
- Existing review and review-round validators continue to pass.
- Skill structure validation passes.
- Pressure scenarios cover the observed model-gate, workspace-trust, Desktop/ACP, permission, and non-JSON-output failures.
- Concurrency fixtures cover two first-session callers, missing, mismatched, and registration-unknown registry/session metadata, lock TOCTOU/PID reuse, identical retry arguments, and a second startup failure ending `await-user` without consuming a review round.
