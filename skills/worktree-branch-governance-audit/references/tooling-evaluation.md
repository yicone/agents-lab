# Tooling Evaluation For Governance Audit Validation

Status: preliminary evaluation, 2026-09-11.

## Repeated Deterministic Failure

Both the 9router and CodexBar audit outputs omitted required Source Inventory fields. CodexBar also relied on an unlisted global skill and attributed its rule to the audit skill. These report-shape and source-integrity failures are mechanically detectable.

## Candidate Fit

- [`agent-sh/agnix`](https://github.com/agent-sh/agnix) validates `AGENTS.md`, `SKILL.md`, hooks, MCP, and other agent configuration formats. Its published rule catalog does not describe validation of arbitrary generated audit reports.
- [`jyablonski/agentslint`](https://github.com/jyablonski/agentslint) checks referential integrity in `AGENTS.md`, including stale paths, broken links, command targets, and undeclared tools. It does not validate this skill's report contract.
- [`agentlint/agentlint`](https://github.com/agentlint/agentlint) scores repository agent readiness and can emit structured reports, but its documented checks target repository context rather than conformance of another agent's generated report.

The name `agentlint` is ambiguous across multiple unrelated projects; any future adoption decision must identify the exact repository and package.

## Decision

- Keep `agnix` and AGENTS-focused linters as candidates for validating the skill and instruction files themselves.
- Do not adopt them as the solution for audit-output conformance without a demonstrated custom-rule or schema path.
- The closest fit for the repeated failure is a small fixture-based validator over a structured audit result. Defer implementation until the report schema is stable enough to avoid encoding churn.
- Tooling status remains `watch`, with evaluation now active. Move to `evaluate-now` for a custom validator when another independent audit repeats a required-field or source-attribution failure after this revision.
