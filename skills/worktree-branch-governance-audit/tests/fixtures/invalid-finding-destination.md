# Governance Audit Report

## Audit Scope

`/workspace/example-repo`.

## Explicit Project Constraints

None discovered.

## Visibility Limits

None.

## Source Inventory

| source | class | scope | normativity | freshness | visibility |
|---|---|---|---|---|---|
| AGENTS.md | instruction | repository | required | current | read |

## Observed Facts And Rules

- A material difference was observed.

## Observed Profile

- primary_branch: main
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

- type: omission
- evidence: A report field is absent from the finding.
- confidence: medium
- recommended action: rewrite

## Unresolved Questions

None.

## Tooling Opportunity

evaluate-now

## Smallest Safe Next Step

Add the destination before publishing.
