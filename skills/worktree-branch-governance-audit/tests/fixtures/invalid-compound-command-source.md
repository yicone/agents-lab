# Governance Audit Report

## Audit Scope

`repo`.

## Explicit Project Constraints

None discovered.

## Task Execution Constraints

Read-only audit.

## Visibility Limits

Remote state was not inspected.

## Source Inventory

| source | class | scope | normativity | freshness | visibility |
|---|---|---|---|---|---|
| `git remote -v` and `git config --get remote.origin.url` | local Git observation | repository | informative | current | read |

## Observed Facts And Rules

One origin remote is configured.

## Observed Profile

- primary_branch: main
- confidence: high
- primary_branch_role: integration
- confidence: medium
- direct_primary_changes: conditional
- confidence: medium
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

none discovered

## Unresolved Questions

None.

## Tooling Opportunity

none

## Smallest Safe Next Step

Split the source invocations into separate rows.
