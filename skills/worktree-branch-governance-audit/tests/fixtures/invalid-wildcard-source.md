# Governance Audit Report

## Audit Scope

`/workspace/example-repo`.

## Explicit Project Constraints

None discovered.

## Task Execution Constraints

Read-only audit.

## Visibility Limits

None.

## Source Inventory

| source | class | scope | normativity | freshness | visibility |
|---|---|---|---|---|---|
| docs/**/*.md | project doc | repository | required | current | read |

## Observed Facts And Rules

- The report relies on one project document.

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
- difference: The source record is aggregated with a wildcard.
- evidence: The source record is aggregated with a wildcard.
- confidence: high
- recommended action: rewrite
- proposed destination: Source Inventory

## Unresolved Questions

None.

## Tooling Opportunity

evaluate-now

## Smallest Safe Next Step

List each relied-on source explicitly.
