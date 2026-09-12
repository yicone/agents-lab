# Governance Audit Report

## Audit Scope

`repo`.

## Explicit Project Constraints

Direct primary changes are prohibited.

## Task Execution Constraints

Read-only audit.

## Visibility Limits

None.

## Source Inventory

| source | class | scope | normativity | freshness | visibility |
|---|---|---|---|---|---|
| `AGENTS.md` | instruction | repository | required | current | read |

## Observed Facts And Rules

The repository rule conflicts with the global default.

## Observed Profile

- primary_branch: main
- confidence: high
- primary_branch_role: integration
- confidence: medium
- direct_primary_changes: prohibited
- confidence: high
- worktree_policy: optional
- confidence: medium
- worktree_adoption: none
- confidence: medium
- worktree_location: unspecified
- confidence: low
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

### Finding 1

- type: project_fit_conflict
- baseline: The global default requires an isolated worktree for every task.
- deviation: The repository explicitly permits work in its checked-out repository.
- difference: The repository rule is narrower than the global default.
- evidence: `AGENTS.md` permits direct work in the checked-out repository.
- confidence: high
- recommended action: keep
- proposed destination: global skill first-hop guidance

## Unresolved Questions

None.

## Tooling Opportunity

none

## Smallest Safe Next Step

Choose an action that addresses the conflicting guidance.
