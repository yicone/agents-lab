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

- Literal branch names are recorded separately from semantic roles.

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
- branch_roles: [feature, fix-*
- confidence: medium
- branch_patterns: [fix/*]
- confidence: high
- upstream_mode: none
- confidence: high
- release_freeze: none
- confidence: high
- environment_coupling: none
- confidence: high

## Difference Findings

### Finding 1

- type: naming_coupling
- evidence: A literal prefix was placed in a semantic role list.
- confidence: high
- recommended action: rewrite
- proposed destination: observed profile schema

## Unresolved Questions

None.

## Tooling Opportunity

watch

## Smallest Safe Next Step

Separate the literal pattern from the semantic role.
