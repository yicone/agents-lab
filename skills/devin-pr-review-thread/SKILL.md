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
- Sandbox callers must use the host broker `python3 scripts/host_provider.py` with a JSON request on stdin; they must not invoke Devin, `gh`, or preflight directly. The broker returns one `devin-host-review/v1` response object.
- The host worker must inherit the host's GitHub transport environment. A wrapper may export `https_proxy`, `http_proxy`, and `all_proxy` before invoking Python; `exec` only replaces the wrapper shell process and is not what makes exported variables visible. Run `host_worker.py --startup-check --repo-root <root> --pr <number>` from that same environment and require `status=ready` before serving requests.
- In sandbox deployments, submit a minimal request (`schema`, `repository_root`, `pr_number`, optional `round`, and bounded `timeout_seconds`) to the host queue consumed by `python3 scripts/host_worker.py`; the worker must be started in a host-side terminal/runtime, not by the sandbox agent. The worker issues the one-shot host authorization after host preflight, then invokes the provider asynchronously. A slow Devin review must not block other requests; read the matching response file and consume only its stable `status` when it appears.
- The consuming agent must not create or repair `round-authorizations.json`; authorization issuance is a host-worker responsibility. The provider owns the one fixed repository session, exact free model, canonical root, read-only permission mode, structured output validation, and GitHub publication.
- On any non-`ok` status, return `await-user` with the protected evidence path. Do not retry, repair, or substitute anything yourself.
- Publish only the provider's validated findings; never paste provider output into a PR thread.

## Workflow

1. **Preflight.** Run the provider preflight and require `status=ok`.
2. **Request structured review.** The provider uses the fixed session and exact model internally, requires the JSON contract in [protocol.md](protocol.md), and keeps all raw output in protected evidence.
3. **Result.** Return the provider's stable result, evidence path, finding count, and published comment references. A successful provider process is not proof that GitHub accepted a comment.
4. **Hand off to round control.** The control thread classifies findings, obtains any required product decision, batches approved fixes, validates them, and selects exactly one next action. This adapter must not fix findings, resolve threads, request another review, or infer that a re-review is wanted.

## Provider-owned state

The provider, not the consuming agent, owns the registry and one-session invariant. The v2 registry is non-secret metadata keyed by normalized `owner/repository`; legacy root-keyed entries must be migrated by host-only enrollment before review. When multiple legacy roots exist, run `python3 scripts/migrate_session_registry.py` once on the host; it creates a mode-0600 backup and atomically rewrites all entries. Consuming agents must not read, write, rotate, or repair it. Host maintainers enroll the GitHub allowlist with `python3 scripts/host_enroll.py --repo-root <root>`, then enroll the fixed Devin session with `python3 scripts/session_enroll.py --repo-root <root> --session-id <id>`. For worktree parents, pass `--worktree-parent <parent>` to both host enrollment and session enrollment. This is host setup, not a consuming-agent step.

## Failure policy

The provider fails closed on unavailable review infrastructure, identity mismatch, malformed output, invalid findings, or unsuccessful publication. It returns a stable status and protected evidence path. The consuming agent must not inspect raw output or attempt a technical recovery; it should report `await-user` and preserve the control-plane round decision.

Preflight reports `github_transport_unavailable` for proxy, TLS, DNS, timeout, or connection failures; `pr_not_found_or_forbidden` for a non-transport `gh` identity failure; and `github_response_invalid` for malformed GitHub output. These statuses identify host connectivity versus PR identity problems without asking the consuming agent to repair either one.

The provider creates review threads only; it does not create a review verdict. Technical recovery rules belong to [runtime-recovery.md](references/runtime-recovery.md) and are not part of the consuming agent contract.

Read [references/review-round-contract.md](references/review-round-contract.md) before starting or continuing a review loop. Provider maintainers may consult [references/runtime-recovery.md](references/runtime-recovery.md) and [references/protocol.md](references/protocol.md); consuming agents should not be asked to operate those internals. Run `scripts/validate_review.py --help` before publishing and `scripts/validate_review_round.py --help` before recording the round.
