# Runtime recovery (maintainer reference)

This file is for the skill implementation and operator diagnostics. A consuming agent should receive only the stable result and evidence path, not these internals.

## Stable result

`preflight.py` emits `devin-pr-review/preflight-v1` JSON. The caller acts only on `status`:

| Status | Caller action |
| --- | --- |
| `ok` | Continue with the authorized review run. |
| `workspace_untrusted`, `model_unavailable`, `model_expired`, `permission_denied`, `session_*`, `*_invalid`, `acp_startup_failed` | Stop and return `await-user` with the protected evidence path. |
| `review-published` | Continue to round-control handoff. |

The caller must not inspect or repair locks, trust stores, process lists, model catalogs, Desktop state, or permission settings. Those belong to the provider runtime.

## ACP and permission gates

An `ACP_READY` response from a minimal no-tool probe proves only transport/session startup. It does not prove that the review prompt can obtain its read-only tools. If the formal review reports `rejected a tool call that requires confirmation` under `auto`, classify it as `permission_denied`, preserve stderr and the non-JSON output in mode-0600 evidence, and publish nothing. Never use `accept-edits` or `dangerous`.

`acp_startup_failed` is reserved for an explicit session/transport startup failure before a model response or tool decision. A timeout, SIGINT, natural-language response, or malformed JSON is not enough to relabel it as an ACP failure; preserve the original status.

## Recovery limits

Only a transport startup failure may receive one retry, using the identical fixed session, model, root, PR head, and permission mode, after a fresh preflight. Model service errors, permission denial, trust uncertainty, malformed output, missing/mismatched sessions, and live or ambiguous locks are non-retryable. A retry is not a new review round.

Devin Desktop is not a prerequisite and must not be opened by the provider. If diagnostics indicate a live client owns the fixed session, stop and request operator action. A stale lock may be handled only by the provider's identity-checked recovery routine; never by the consuming agent.
