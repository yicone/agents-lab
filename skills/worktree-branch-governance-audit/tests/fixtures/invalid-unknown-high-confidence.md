# Governance Audit Report

## Audit Scope

`/workspace/example-repo`.

## Explicit Project Constraints

None discovered.

## Visibility Limits

Remote state was not inspected.

## Source Inventory

| source | class | scope | normativity | freshness | visibility |
|---|---|---|---|---|---|
| AGENTS.md | instruction | repository | required | current | read |

## Observed Facts And Rules

- Remote lifecycle remains unverified.

## Observed Profile

- primary_branch: unknown
- confidence: high
- primary_branch_role: production
- confidence: high
- direct_primary_changes: prohibited
- confidence: high
- worktree_policy: optional
- confidence: medium
- worktree_adoption: none
- confidence: high
- worktree_location: unspecified
- confidence: high
- branch_roles: [feature]
- confidence: medium
- branch_patterns: [feature/*]
- confidence: high
- upstream_mode: none
- confidence: high
- release_freeze: none
- confidence: high
- environment_coupling: none
- confidence: high

## Difference Findings

### Finding 1

- type: unverifiable_claim
- evidence: The primary branch was not inspected.
- confidence: high
- recommended action: clarify
- proposed destination: unresolved questions

## Unresolved Questions

Confirm the primary branch from repository evidence.

## Tooling Opportunity

watch

## Smallest Safe Next Step

Inspect local branch metadata without fetching.
