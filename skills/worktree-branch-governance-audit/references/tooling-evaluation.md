# Tooling Evaluation For Governance Audit Validation

Status: evaluate-now, 2026-09-12.

## Repeated Deterministic Failure

The 9router and CodexBar audit outputs omitted required Source Inventory fields. CodexBar also relied on an unlisted global skill and attributed its rule to the audit skill. The AICenter audit independently repeated report-contract failures: a non-`main` production/integration branch was forced into a name-coupled profile, literal branch patterns were placed in semantic roles, a relied-on plan was hidden under a wildcard source row, `unknown` received high confidence, and an enforcement recommendation lacked proportionality evidence. These report-shape and source-integrity failures are mechanically detectable.

## Candidate Fit

- [`agent-sh/agnix`](https://github.com/agent-sh/agnix) validates `AGENTS.md`, `SKILL.md`, hooks, MCP, and other agent configuration formats. Its published rule catalog does not describe validation of arbitrary generated audit reports.
- [`jyablonski/agentslint`](https://github.com/jyablonski/agentslint) checks referential integrity in `AGENTS.md`, including stale paths, broken links, command targets, and undeclared tools. It does not validate this skill's report contract.
- [`agentlint/agentlint`](https://github.com/agentlint/agentlint) scores repository agent readiness and can emit structured reports, but its documented checks target repository context rather than conformance of another agent's generated report.

The name `agentlint` is ambiguous across multiple unrelated projects; any future adoption decision must identify the exact repository and package.

## Decision

- Keep `agnix` and AGENTS-focused linters as candidates for validating the skill and instruction files themselves.
- Do not adopt them as the solution for audit-output conformance without a demonstrated custom-rule or schema path.
- The closest fit for the repeated failure is the local dependency-free fixture-based validator at `scripts/validate_report.py`. It validates report structure, source-table shape, profile keys/enums, confidence constraints, conditional extension completeness, and finding fields; it does not decide semantic project fit or evidence truth.
- The local report validator is now `evaluate-now` because an independent post-revision audit repeated mechanically detectable contract failures; `agnix` and AGENTS-focused linters remain candidates for validating the skill and instruction files themselves.
