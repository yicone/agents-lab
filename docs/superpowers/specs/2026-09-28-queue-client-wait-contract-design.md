# Queue Client Wait Contract Design

## Problem

A consuming agent started `queue_client.py`, received no immediate stdout, failed to inspect the running terminal, queried GitHub before publication, and reported provider failure. The original queue client process later completed successfully with `status=review-published`. Registration, trust, host worker, provider validation, and GitHub publication were all healthy.

## Decision

Keep the queue client's protocol unchanged: stdout remains exactly one final `devin-host-review/v1` JSON object. Strengthen the consuming-agent contract instead.

A caller that receives a running process/session handle must keep waiting on that same process until it exits or the queue client's own bounded wait produces its normalized response. Temporary absence of stdout, inability to inspect an app terminal, and an early GitHub query are not provider results and must not be converted into `await-user` or a failed round.

Only the final parsed queue-client JSON controls the review outcome. If the surrounding harness imposes a shorter execution window, it must resume/poll the same process rather than submit another request. The existing prohibition on automatic retries remains unchanged.

## Scope

- Update the public consuming-agent contract in `SKILL.md`.
- Add the observed race to `pressure-scenarios.md` as a required behavior check.
- Add a lightweight documentation regression test so the critical contract cannot disappear unnoticed.
- Do not add progress output, change the queue JSON schema, change timeouts, or split submit/status operations.

## Verification

- The pressure scenario must distinguish a pending process from a completed normalized response.
- The regression test must require the skill to say that callers wait on the same process and do not infer failure from missing intermediate stdout.
- Existing queue client and provider tests must continue to pass.
