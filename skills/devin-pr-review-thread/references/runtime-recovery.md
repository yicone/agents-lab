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

## Host service lifecycle

On macOS the host maintainer installs the worker once as a login-persistent LaunchAgent:

```bash
python3 scripts/install_launchd_service.py
launchctl print "gui/$UID/com.tr.agentslab.devin-pr-review-thread"
```

The service must point at the canonical skill path, use the host proxy environment, and remain resident while it polls `/private/tmp/devin-host-review`. It does not embed a PR number; every request is preflighted independently. After replacing skill scripts, reinstall/kickstart the LaunchAgent and verify the emitted evidence contains `discovery_strategy: db-hinted-sequential-worktrees-v2`. Stop any older manually launched worker only after the new LaunchAgent is loaded and healthy.

The Devin model catalog is a slow host operation. `preflight.py` gives `devin models list --format json` a 60-second command budget and `devin list --format json` a 30-second command budget inside the overall 90-second preflight ceiling; a shorter caller-side timeout must not reinterpret either result as invalid session/model metadata.

Large PR patches use the GitHub REST diff media type first, with `gh pr diff` as a fallback. Each path may make at most three attempts for a transport-shaped failure (TLS, timeout, proxy, connection, DNS, or EOF); authentication, identity, and HTTP errors are not retried. An exhausted transport failure is preserved in evidence as `github_transport_unavailable`; a successful patch fetch is parsed once and reused for both changed-line validation and the Devin prompt. The prompt is passed through Devin's mode-0600 `--prompt-file`, not argv, so a large patch cannot hit the host's argument-size limit.

The queue is single-consumer. The login worker owns a queue lock and atomically claims each request before dispatch; a diagnostic `host_worker.py --startup-check` must use `--check-only`, because without it the command continues serving the queue. If a duplicate worker is started, it exits with `worker_already_running` and leaves the login service untouched. Worker-side dispatch or provider-output failures preserve the request identity and write protected evidence instead of returning a null, untraceable response.
