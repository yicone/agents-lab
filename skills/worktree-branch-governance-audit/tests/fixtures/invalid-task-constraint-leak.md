# Governance Audit Report

## Audit Scope

`repo`.

## Explicit Project Constraints

The project requires sibling worktrees; this audit is read-only.

## Task Execution Constraints

Do not create a worktree or fetch remotes.

## Visibility Limits

Remote state was not inspected.

## Source Inventory

| source | class | scope | normativity | freshness | visibility |
|---|---|---|---|---|---|
| `AGENTS.md` | instruction | repository | required | current | read |

## Observed Facts And Rules

The repository requires sibling worktrees.

## Observed Profile

- primary_branch: main
- confidence: high
- primary_branch_role: integration
- confidence: medium
- direct_primary_changes: prohibited
- confidence: high
- worktree_policy: required
- confidence: high
- worktree_adoption: active
- confidence: high
- worktree_location: sibling
- confidence: high
- branch_roles: [feature]
- confidence: medium
- branch_patterns: [feature/*]
- confidence: medium
- upstream_mode: none
- confidence: high
- release_freeze: none
- confidence: medium
- environment_coupling: none
- confidence: high

## Difference Findings

none discovered

## Unresolved Questions

None.

## Tooling Opportunity

none

## Smallest Safe Next Step

Keep task mechanics under Task Execution Constraints.
