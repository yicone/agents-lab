# Devin Host-Side Review Provider Design

## Goal

Allow sandboxed agent harnesses to request the repository's bounded Devin PR review without receiving Devin, ACP, GitHub, credential, or host-filesystem capabilities. A host-side command owns the existing `devin-pr-review-thread` provider and returns only a stable result plus a protected evidence reference.

## Scope and Non-Goals

The provider accepts one review request for an existing repository and PR, runs the existing preflight and bounded provider workflow, validates findings, and publishes idempotent GitHub review threads. It does not fix findings, resolve threads, merge PRs, rotate sessions, change models, change permission modes, trust workspaces, or repair runtime state automatically.

## Boundary

The sandbox-facing command is a narrow request/response interface. The request is JSON on stdin; the response is exactly one JSON object on stdout. Human diagnostics go to stderr. The provider runs with a host-side working directory and explicitly selected environment; it must not inherit arbitrary prompt text as shell code or expose its environment to the caller.

### Request

```json
{
  "schema": "devin-host-review/v1",
  "repository_root": "/Users/me/src/repo",
  "pr_number": 123,
  "round": 1,
  "authorization": {
    "action": "run",
    "authorization_id": "opaque-single-use-id",
    "head_sha": "40-hex-sha"
  },
  "timeout_seconds": 900
}
```

The host worker accepts a minimal review request and issues a host-owned `pr-review-round/v1` `phase=run` authorization after preflight. The provider itself accepts only that authorization with a stable opaque single-use `authorization_id`, canonical repository binding, PR/head/round binding, and finite-budget eligibility. Consumers never create or repair the authorization ledger. The provider rejects self-asserted or missing authorization, path traversal, non-absolute roots, stale head SHA, invalid round numbers, unknown keys that could carry commands, and timeouts outside a bounded range. The canonical root must also be in a host-side allowlist bound to the expected GitHub owner/name.

### Response

```json
{
  "schema": "devin-host-review/v1",
  "status": "review-published|no-findings|await-user|provider-unavailable|invalid-request",
  "repository_root": "/Users/me/src/repo",
  "pr_number": 123,
  "head_sha": "40-hex-sha-or-null",
  "round": 1,
  "findings_count": 0,
  "comments": [],
  "evidence_ref": "opaque-evidence-id-or-null",
  "retryable": false
}
```

All keys are always present; unavailable values are JSON `null`. The response contains no raw Devin output, credentials, prompts, tokens, lock paths, process details, model catalog, or trust-store contents. `comments` contains only accepted `{"id": string, "url": string}` refs, and `findings_count` is the number of validated findings. Partial publication returns `await-user` with accepted refs only. `evidence_ref` is opaque and never exposes a host filesystem path; full evidence is host-owned, mode-restricted, and retained by policy.

## State and Safety

1. Validate the request, trusted authorization artifact, and host repository allowlist.
2. Run `devin-pr-review-thread` preflight in the host runtime.
3. If preflight is not `ok`, return `await-user` or `provider-unavailable` with an opaque evidence reference; do not repair or retry from the sandbox request.
4. Run exactly one authorized provider invocation. A transport-only retry remains internal and bounded by the existing runtime recovery contract; it does not create another round.
5. Validate and idempotently publish findings through the existing provider.
6. Return the stable response and record evidence outside the repository.

The provider uses an OS-level cross-process lock keyed by canonical repository root and a durable atomic ledger with `reserved`, `running`, `publishing`, and `complete` states. A second request for the same root returns `provider-unavailable`; the sandbox must never act on `retryable`. A repeated request with the same stable `authorization_id` is idempotent and must not run Devin twice. Crash recovery reconciles the ledger with existing GitHub markers and never reruns an authorization whose publication is uncertain.

## Capability and Credential Boundary

Only the host provider may access `devin`, `gh`, the fixed-session registry, trust metadata, session locks, and protected evidence. The sandbox caller receives no tokens, environment dump, subprocess output, raw stderr, or arbitrary command result. stdout and stderr are sanitized protocol channels; full diagnostics belong only in protected evidence. The provider invokes fixed allowlisted commands and passes structured arguments, never a shell-evaluated string.

## Failure Mapping

| Internal condition | Sandbox response |
| --- | --- |
| Invalid request or stale authorization | `invalid-request` |
| Preflight/model/GitHub/session/trust/permission failure | `await-user` |
| Host runtime unavailable, timeout, or provider busy | `provider-unavailable` |
| Valid run with zero findings | `no-findings` |
| Valid run and one or more accepted comments | `review-published` |

The caller must not interpret internal error text or choose recovery actions. Human operators use the opaque evidence reference through a host-side inspection path and the maintainer runtime reference. `retryable` is informational for the host supervisor only; sandbox callers never retry based on it.

## Verification

- Contract tests reject malformed requests, stale heads, unknown fields, path escapes, and out-of-range timeouts.
- Boundary tests prove stdout/stderr contain only sanitized protocol data and no secrets or raw subprocess output.
- Authorization tests reject self-asserted, stale, cross-repository, and over-budget round records.
- Allowlist tests bind canonical roots to the expected GitHub owner/name.
- Concurrency tests prove one in-flight request per canonical root, durable crash reconciliation, and idempotent repeated authorization IDs.
- Sandbox tests prove provider failures become stable statuses without requiring the caller to access Devin or GitHub.
- Existing preflight, finding, and review-round validators remain green.
