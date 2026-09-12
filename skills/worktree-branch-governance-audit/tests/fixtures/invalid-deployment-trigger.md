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

- Production deploys from a named branch.

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
- deployment_topology: single-source
- confidence: high
- deployment_bindings: [site=branch:main]
- confidence: high
- deployment_triggers: [site=schedule]
- confidence: high
- preview_behavior: pull-request-preview
- confidence: high

## Difference Findings
none discovered

## Unresolved Questions

Confirm the provider's production binding.

## Tooling Opportunity

evaluate-now

## Smallest Safe Next Step

Validate the provider binding read-only.
