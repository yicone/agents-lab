# Governance Audit Report

## Audit Scope
`repo`.
## Explicit Project Constraints
none discovered
## Task Execution Constraints
read-only audit
## Visibility Limits
deployment not inspected
## Source Inventory
| source | class | scope | normativity | freshness | visibility |
|---|---|---|---|---|---|
| `docker-compose.yml` | runtime | repository | informative | current | read |
## Observed Facts And Rules
runtime is worktree-bound
## Observed Profile
- primary_branch: main
- confidence: high
- primary_branch_role: release-ready
- confidence: medium
- direct_primary_changes: conditional
- confidence: medium
- worktree_policy: required
- confidence: high
- worktree_adoption: active
- confidence: high
- worktree_location: repository-local
- confidence: high
- branch_roles: [feature]
- confidence: medium
- branch_patterns: [feature/*]
- confidence: medium
- upstream_mode: none
- confidence: high
- release_freeze: none
- confidence: medium
- environment_coupling: docker
- confidence: high
- runtime_binding: worktree
- confidence: high
## Difference Findings
none discovered
## Unresolved Questions
none
## Tooling Opportunity
none
## Smallest Safe Next Step
none
