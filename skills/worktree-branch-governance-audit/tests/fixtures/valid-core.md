# Governance Audit Report

## Audit Scope

`/workspace/example-repo`.

## Explicit Project Constraints

The repository owner permits direct work in the checked-out repository.

## Task Execution Constraints

- Read-only audit; do not create a worktree or contact remotes.

## Visibility Limits

Remote lifecycle was not inspected.

## Source Inventory

| source | class | scope | normativity | freshness | visibility |
|---|---|---|---|---|---|
| AGENTS.md | instruction | repository | required | current | read |

## Observed Facts And Rules

- The repository has one checked-out worktree.

## Observed Profile

- primary_branch: aimaiai
- confidence: high
- primary_branch_role: integration
- confidence: high
- direct_primary_changes: conditional
- confidence: medium
- worktree_policy: optional
- confidence: medium
- worktree_adoption: active
- confidence: high
- worktree_location: repository-local
- confidence: medium
- branch_roles: [feature, fix, release]
- confidence: medium
- branch_patterns: [codex/*, release/*]
- confidence: high
- upstream_mode: selective-adoption
- confidence: medium
- release_freeze: on-demand
- confidence: low
- environment_coupling: none
- confidence: high

## Difference Findings

### Finding 1

- type: project_fit_conflict
- difference: The repository-specific rule is narrower than the global default.
- evidence: The repository-specific rule is narrower than the global default.
- confidence: high
- recommended action: narrow_global_default
- proposed destination: global skill first-hop guidance

## Unresolved Questions

None.

## Tooling Opportunity

evaluate-now

## Smallest Safe Next Step

Review the report with the repository owner.
