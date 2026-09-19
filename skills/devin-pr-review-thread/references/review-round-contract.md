# Bounded review-round contract

This contract separates a review **provider** from the review **control plane**. `devin-pr-review-thread` is a provider: it can produce and publish one set of validated findings. A repository's `pr-review-fix` workflow (or the human control thread applying this contract) owns triage, changes, validation, and the decision to request another run.

The contract borrows the finite-loop policy from `pr-review-fix`; it does not import that repository-specific skill or make it a prerequisite for this one.

## Required order

1. Before a provider run, record `run` with the current PR head and the reason this round is allowed.
2. Run one provider and publish only its validated findings.
3. The control thread classifies each finding as `must-fix`, `should-fix`, `product-decision`, or `ignore`.
4. Obtain human direction for every `product-decision` and ambiguous `should-fix`. Batch approved items from this round, make only those changes, and run relevant validation.
5. Record one post-triage action: `fix-and-rereview`, `fix-and-stop`, `stop`, `follow-up`, or `await-user`.
6. Start another provider run only if the prior record says `fix-and-rereview`, its validation evidence is recorded, and the next round is allowed by the budget below.

The record is an auditable decision, not a background task or a merge approval. It does not authorize resolving a GitHub thread, changing scope, or merging a PR. Provider markers include the reviewed `head_sha`, so the same fixed session can safely reuse finding IDs on a later head.

## Finite budget

| Round | Eligible work | May select `fix-and-rereview`? |
| --- | --- | --- |
| 1 | Confirmed `must-fix` and in-scope `should-fix` | Yes, after batching fixes and validation |
| 2 | New correctness, spec, security/privacy, data-loss, accessibility, or low-risk maintainability issues | Yes, only if that work remains after triage |
| 3+ | `must-fix`, a regression introduced by review fixes, or an explicitly accepted `should-fix` | Yes, only with the matching exception |

Stop the loop when relevant CI/local validation and required QA are green, and remaining comments are stale, duplicate, cosmetic, low-confidence, out of scope, or tracked as follow-up work. Never stop for security, privacy, data-loss, backup/migration integrity, parser correctness, failed CI, active spec violations, or required QA blockers without a concrete verified disposition.

## Record format

Write a JSON document outside the repository or in a PR-specific protected evidence location. Validate it with `scripts/validate_review_round.py`.

```json
{
  "schema": "pr-review-round/v1",
  "pr": {
    "repository": "owner/repo",
    "number": 123,
    "head_sha": "0123456789abcdef0123456789abcdef01234567"
  },
  "round": 2,
  "phase": "post-triage",
  "reviewer": {
    "provider": "devin",
    "session_id": "fixed-session-id",
    "model": "SWE-2 High"
  },
  "findings": {
    "must-fix": ["F-001"],
    "should-fix": [],
    "product-decision": [],
    "ignore": ["F-002"]
  },
  "decision": {
    "action": "fix-and-rereview",
    "reason": "F-001 is a correctness defect fixed in the same batch.",
    "validation": ["scripts/validate_review.py passed", "targeted test passed"]
  }
}
```

`phase=run` reserves one provider invocation and must use `action=run`; it does not contain findings or validation. `phase=post-triage` records the outcome of exactly one completed run. Finding identifiers must occur in exactly one classification bucket. The `validation` list is mandatory only for `fix-and-rereview`; it is evidence that the next run is reviewing a coherent fixed head rather than an intermediate patch.

For a third or later `fix-and-rereview`, add `exception` with exactly one of `must-fix`, `review-fix-regression`, or `accepted-should-fix`. The exception explains why the normal two-round budget is extended; it is not a generic escape hatch.

## Ownership boundaries

- **Provider (Devin adapter):** fixed session, structured findings, safety validation, idempotent GitHub review-thread publishing, and run evidence.
- **Control thread / `pr-review-fix`:** thread-aware retrieval, classification, human-decision gate, batching fixes, local/CI/QA validation, round record, and next-review decision.
- **Human maintainer:** product/scope choices, ambiguous should-fix choices, explicit thread resolution authorization, and merge acceptance.
