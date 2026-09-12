# Governance Audit Report
## Audit Scope
`repo`.
## Explicit Project Constraints
none
## Task Execution Constraints
read-only
## Visibility Limits
none
## Source Inventory
| source | class | scope | normativity | freshness | visibility |
|---|---|---|---|---|---|
| `AGENTS.md` | instruction | repository | required | current | read |
## Observed Facts And Rules
none
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
- type: enforcement_gap
- difference: Direct primary writes are not mechanically rejected.
- evidence: no effective control was found
- confidence: high
- recommended action: enforce_mechanically
- proposed destination: repository hook
## Unresolved Questions
none
## Tooling Opportunity
none
## Smallest Safe Next Step
none
