# Governance Audit Report

## Audit Scope

`/workspace/example-repo`.

## Explicit Project Constraints

Production changes require review.

## Task Execution Constraints

- Read-only audit; do not create a worktree or contact remotes.

## Visibility Limits

The deployment provider API was not inspected.

## Source Inventory

| source | class | scope | normativity | freshness | visibility |
|---|---|---|---|---|---|
| .github/workflows/deploy.yml | deployment fact | repository | current state | current | read |

## Observed Facts And Rules

- Components have distinct release sources.

## Observed Profile

- primary_branch: trunk
- confidence: high
- primary_branch_role: production
- confidence: high
- direct_primary_changes: prohibited
- confidence: high
- worktree_policy: conditional
- confidence: medium
- worktree_adoption: none
- confidence: high
- worktree_location: unspecified
- confidence: high
- branch_roles: [feature, hotfix, release]
- confidence: medium
- branch_patterns: [feature/*, hotfix/*]
- confidence: high
- upstream_mode: none
- confidence: high
- release_freeze: none
- confidence: medium
- environment_coupling: production
- confidence: high
- deployment_topology: component-specific
- confidence: high
- deployment_bindings: [api=branch:main, admin=branch:main, miniapp=branch-pattern:release/miniapp-*]
- confidence: high
- deployment_triggers: [api=manual, admin=manual]
- confidence: high
- preview_behavior: environment-preview
- confidence: medium

## Difference Findings
none discovered

## Unresolved Questions

Confirm the provider bindings read-only.

## Tooling Opportunity

evaluate-now

## Smallest Safe Next Step

Validate the provider bindings read-only.
