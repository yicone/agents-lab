---
name: devin-pr-review-thread
description: Use when another agent harness must review a GitHub PR through the local Devin CLI, reuse one Devin session per repository, and publish actionable findings as GitHub review threads.
metadata:
  owner: agents-lab
  scope: repo
  version: "0.2.0"
---

# Devin PR Review Thread

This is a black-box adapter between an agent harness, a local review provider, and GitHub. The harness supplies an authorized PR review request and receives a stable result plus protected evidence; it does not operate Devin, ACP, Desktop, model catalogs, trust stores, locks, or permission settings.

It is not a review-loop controller. A harness may run this adapter only after the control thread has recorded an explicit bounded review-round decision under [review-round-contract.md](references/review-round-contract.md). A new commit, a fixed finding, or an unresolved thread alone is never an automatic reason to invoke Devin again.

## Contract for consuming agents

- Run the provider through its preflight interface: `python3 scripts/preflight.py --repo-root <root> --pr <number>`. Consume the JSON `status`; do not reinterpret it.
- Sandbox callers must submit a minimal `devin-host-review/v1` request file to the host worker queue; they must not invoke `host_provider.py` directly, Devin, `gh`, or preflight. `host_provider.py` is an internal worker component and requires host-issued authorization. The worker returns one `devin-host-review/v1` response object.
- The host worker must inherit the host's GitHub transport environment. A wrapper may export `https_proxy`, `http_proxy`, and `all_proxy` before invoking Python; `exec` only replaces the wrapper shell process and is not what makes exported variables visible. Run `host_worker.py --startup-check --repo-root <root> --pr <number>` from that same environment; it prints `status=ready` and then remains running to consume requests. Use `--check-only` only for a diagnostic probe.
- In sandbox deployments, submit a minimal request (`schema`, `repository_root`, `pr_number`, optional `round`, and bounded `timeout_seconds`) to the host queue consumed by `python3 scripts/host_worker.py`; the worker must be started in a host-side terminal/runtime, not by the sandbox agent. The worker issues the one-shot host authorization after host preflight, then invokes the provider asynchronously. A slow Devin review must not block other requests; read the matching response file and consume only its stable `status` when it appears.
- Keep the two JSON protocols separate. The control-plane record validated by `validate_review_round.py` uses `schema: pr-review-round/v1` and is never sent to the worker/provider. The queue request uses only `schema: devin-host-review/v1`, `repository_root`, `pr_number`, optional `round`, and `timeout_seconds`; do not add `authorization` because the worker creates it. A request can be represented as: `{"schema":"devin-host-review/v1","repository_root":"/absolute/repo","pr_number":232,"round":2,"timeout_seconds":900}`.
- The consuming agent must not create or repair `round-authorizations.json`; authorization issuance is a host-worker responsibility. The provider owns the one fixed repository session, exact free model, canonical root, read-only permission mode, structured output validation, and GitHub publication.
- The consuming agent is a black-box client: it should submit a minimal request and consume the stable response. Reading `host_worker.py`, `host_provider.py`, or preflight implementation is not part of the review workflow; implementation diagnosis belongs to host maintainers.
- On any non-`ok` status, return `await-user` with the protected evidence path and consume the stable top-level `failure_code` for host diagnosis. Every `await-user` response must include both `failure_code` and `evidence_ref` unless the request was rejected before evidence could be created; in that case `failure_code` still identifies the rejection. Do not retry, repair, or substitute anything yourself.
- Publish only the provider's validated findings; never paste provider output into a PR thread.

## Workflow

1. **Preflight.** Run the provider preflight and require `status=ok`.
2. **Request structured review.** The provider uses the fixed session and exact model internally, requires the JSON contract in [protocol.md](protocol.md), and keeps all raw output in protected evidence.
3. **Result.** Return the provider's stable result, evidence path, finding count, and published comment references. A successful provider process is not proof that GitHub accepted a comment.
4. **Hand off to round control.** The control thread classifies findings, obtains any required product decision, batches approved fixes, validates them, and selects exactly one next action. This adapter must not fix findings, resolve threads, request another review, or infer that a re-review is wanted.

## Provider-owned state

The provider, not the consuming agent, owns the registry and one-session invariant. The v2 registry is non-secret metadata keyed by normalized `owner/repository`; legacy root-keyed entries must be migrated by host-only enrollment before review. When multiple legacy roots exist, run `python3 scripts/migrate_session_registry.py` once on the host; it creates a mode-0600 backup and atomically rewrites all entries. Consuming agents must not read, write, rotate, or repair it. Host maintainers enroll the GitHub allowlist with `python3 scripts/host_enroll.py --repo-root <root>`, then enroll the fixed Devin session with `python3 scripts/session_enroll.py --repo-root <root> --session-id <id>`. If the registered main workspace is separate from a worktree parent, enroll both the exact main root and the parent wildcard. For worktree parents, pass `--worktree-parent <parent>` to session enrollment. This is host setup, not a consuming-agent step.
Devin's session `working_directory` is mutable: resuming the same session from a different cwd can move it between the main workspace and a nested worktree. Preflight therefore searches every enrolled repository worktree without filtering by the current PR head, reports the observed root separately from the registered root, and the provider always resumes Devin from the registered main workspace. The request root identifies the repository/PR only; it is not the Devin execution cwd. This preserves one session and one repository binding across PRs and worktrees.
The host worker is a long-lived process. After updating this skill's scripts, the host maintainer must restart the worker from the canonical skill path; its preflight evidence reports `discovery_strategy: all-enrolled-worktrees-no-head-filter-v1` so stale worker copies are diagnosable. A transient session move during the workspace-scoped scan receives one bounded second discovery pass before becoming `session_missing`.

## Failure policy

The provider fails closed on unavailable review infrastructure, identity mismatch, malformed output, invalid findings, or unsuccessful publication. It returns a stable status and protected evidence path. The consuming agent must not inspect raw output or attempt a technical recovery; it should report `await-user` and preserve the control-plane round decision.

Preflight reports `github_transport_unavailable` for proxy, TLS, DNS, timeout, or connection failures; `pr_not_found_or_forbidden` for a non-transport `gh` identity failure; and `github_response_invalid` for malformed GitHub output. These statuses identify host connectivity versus PR identity problems without asking the consuming agent to repair either one.

The provider creates review threads only; it does not create a review verdict. Technical recovery rules belong to [runtime-recovery.md](references/runtime-recovery.md) and are not part of the consuming agent contract.

Read [references/review-round-contract.md](references/review-round-contract.md) before starting or continuing a review loop. Provider maintainers may consult [references/runtime-recovery.md](references/runtime-recovery.md) and [references/protocol.md](references/protocol.md); consuming agents should not be asked to operate those internals. Run `scripts/validate_review.py --help` before publishing and `scripts/validate_review_round.py --help` before recording the round.
