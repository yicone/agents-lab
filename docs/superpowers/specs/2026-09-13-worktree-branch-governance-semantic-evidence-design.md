# Worktree Branch Governance Semantic Evidence Design

## Status

Approved in conversation on 2026-09-13. This is the second corrective increment following the independent YR replay. It extends the prior schema-validator and YR-regression designs without changing the audit's read-only boundary.

## Problem

The second YR replay independently loaded the revised skill, inspected the repository, wrote a new temporary report, and eventually passed the structural validator. The report nevertheless invented an upstream relationship, weakened an explicit repository worktree policy because the audit itself did not use a worktree, represented component-specific deployment sources in one scalar branch field, and emitted two positive observations as Difference Findings.

The replay also grouped two Git commands in one Source Inventory row and produced a literal filename containing `$(date +%s)`. These failures show that the current validator catches report syntax but the schema still invites unsupported semantics and cannot represent a monorepo with component-specific release sources.

## Considered Approaches

### Add more prose while preserving the current fields

This is smallest, but `production_branch` cannot truthfully hold both a literal branch and a component-to-pattern mapping. More prose would not correct the data model.

### Add warnings only

Warnings could flag suspicious values without breaking reports, but this experimental skill has no compatibility requirement and should reject shapes already known to produce misleading conclusions.

### Revise the deployment model and strengthen evidence-bearing fields

Chosen. Replace the scalar deployment fields with a compact component-aware extension, make findings expose their comparison basis, remove `keep` as a difference action, and add narrow deterministic checks for unsupported upstream fields and compound command sources.

## Project Policy Versus Audit Execution

`worktree_policy` describes the repository's policy for the work it governs. A read-only audit instruction not to create or use a worktree does not change that policy from `required` to `conditional`.

Task execution constraints must appear only in `Task Execution Constraints`, `Audit Scope`, or `Visibility Limits`. A sentence or bullet in `Explicit Project Constraints` must not append run-local clauses such as “this audit is read-only,” “do not fetch,” or “write the report under `/private/tmp`.”

The validator may implement narrow phrase checks for known audit-mechanics leakage, but the behavioral regression remains authoritative because equivalent wording cannot be exhaustively recognized.

## Upstream Evidence Gate

The following are sufficient to activate fork/upstream semantics:

- an applicable repository or user rule naming an upstream relationship;
- a configured upstream remote or another explicitly identified upstream source;
- an authoritative project document describing mirror, periodic sync, selective adoption, or upstream contribution flow.

The following are not sufficient by themselves:

- the existence of a single `origin` remote;
- ordinary third-party dependencies;
- generic contribution language;
- a branch name that merely resembles an upstream workflow.

Without sufficient evidence, use `upstream_mode: none` when absence is directly established, or `unknown` when the relevant surface was not inspected. Do not emit the fork extension. The validator cannot prove repository evidence from the report alone, so this rule is enforced primarily by the skill and the YR behavioral regression.

## Component-Specific Deployment Extension

Replace the scalar deployment extension:

```text
production_branch
production_trigger
preview_behavior
```

with:

```text
deployment_topology: single-source | component-specific | external | unknown
deployment_bindings: [<component>=branch:<literal>, <component>=branch-pattern:<literal>, <component>=tag-pattern:<literal>, ...]
deployment_triggers: [<component>=merge|push|tag|manual|external|unknown, ...]
preview_behavior: none | branch-preview | pull-request-preview | environment-preview | mixed | unknown
```

Each field retains one adjacent `confidence`. This extension remains conditional and all-or-nothing.

Examples:

```text
deployment_topology: component-specific
confidence: high
deployment_bindings: [api=branch:main, admin=branch:main, miniapp=branch-pattern:release/miniapp-*]
confidence: high
deployment_triggers: [api=manual, admin=manual, miniapp=manual]
confidence: high
preview_behavior: environment-preview
confidence: medium
```

The values describe release-source policy, not proof that a live deployment succeeded. Provider or runtime status remains an observed fact with its own visibility limit.

The validator checks that `deployment_bindings` and `deployment_triggers` are lists, that each item has a component key, and that binding and trigger component sets match. It checks binding prefixes and trigger enums but does not verify the referenced workflow.

## Difference Evidence Shape

Every finding must contain:

```text
type:
baseline:
deviation:
difference:
evidence:
confidence:
recommended_action:
proposed_destination:
```

`baseline` identifies the applicable rule, candidate profile, documented expectation, or source claim. `deviation` identifies the conflicting source, current evidence, missing guidance destination, or unsupported claim. `difference` summarizes their relationship.

For `omission`, the deviation may explicitly be `missing guidance` plus the evidence that makes the omission material. For `unverifiable_claim`, baseline is the claim and deviation is the stated visibility gap. Empty, identical, or generic values such as “correct behavior” are invalid.

Remove `keep` from allowed finding actions. When sources align or the audit correctly follows evidence discipline, record that under `Observed Facts And Rules`; it is not a difference. If one side of a real conflict should remain unchanged, the action must name what happens to the other side, such as `rewrite`, `retire`, `clarify`, or `narrow_global_default`.

An unregistered directory beside registered worktrees is an observed filesystem fact, not automatically `state_policy_confusion`, `stale_guidance`, or `enforcement_gap`. It becomes a finding only when an applicable source incorrectly presents the directory as registered/current policy, or when a material guidance omission is established.

## Source Inventory Command Precision

One row represents one source invocation or artifact. Reject source cells that join multiple inline-code source tokens with connectors such as `and`, `+`, `/`, or commas when the tokens are paths, Git commands, GitHub commands, or other inspected commands.

Valid:

```text
| `git remote -v` | local Git observation | ... |
| `git config --get remote.origin.url` | local Git observation | ... |
```

Invalid:

```text
| `git remote -v` and selected `git config --get` | local Git observation | ... |
```

The validator should inspect inline-code spans rather than rejecting ordinary prose containing the word “and.”

## Temporary Report Naming

The skill must tell agents to obtain a concrete temporary path before writing:

```bash
report_path=$(mktemp -t branch-governance-audit)
```

The shell prints and assigns the resolved path. Every later write, validator invocation, and final link must use that exact captured value. Do not embed unexpanded shell substitutions inside a quoted filename or patch path.

This operation is allowed only outside the audited repository and does not authorize target-repository writes.

## YR Regression Extension

Extend YR Case 7 with the second independent replay baseline:

- `Explicit Project Constraints` mixed a run-local read-only clause into a repository rule;
- explicit required worktree policy became conditional because the audit did not use a worktree;
- a single-origin repository acquired unsupported selective-adoption and upstream-contribution fields;
- API/admin and mini-program release sources were concatenated into one scalar branch field;
- correct cached-ref discipline became an `unverifiable_claim` finding with action `keep`;
- non-Git sibling residue became a finding without a conflicting or exhaustive source claim;
- two commands were grouped in one Source Inventory row;
- the report path contained a literal shell substitution.

Passing Case 7 requires no unsupported upstream extension, faithful project policy strength, component-specific deployment bindings when relevant, no positive-alignment findings, one source per row, and a concrete temporary filename.

## Validator And Fixture Changes

Add or revise fixtures for:

- valid single-source deployment;
- valid component-specific deployment;
- mismatched deployment component sets;
- malformed deployment binding and trigger items;
- removed scalar deployment fields;
- a finding missing baseline or deviation;
- a finding using removed action `keep`;
- compound Git command sources;
- known task-mechanics leakage into Explicit Project Constraints;
- a concrete temporary report example.

Existing fixtures must continue to isolate their intended diagnostics. The validator must remain dependency-free and report line-oriented diagnostics.

## Tooling Boundary

The local validator remains the correct tool for report-contract checks. The new upstream-evidence and positive-alignment rules still require behavioral evaluation. `agentslint` and `agnix` remain deferred because neither validates generated report semantics or component deployment mappings.

## Success Criteria

- The second independent YR replay is preserved as a semantic RED baseline.
- No scalar field accepts several component branch bindings disguised as prose.
- Deployment binding and trigger component sets are mechanically consistent.
- Findings expose distinct baseline and deviation fields and cannot use `keep`.
- Compound command sources are rejected.
- The skill explicitly preserves repository policy strength across read-only audit constraints.
- Temporary report paths are concrete rather than literal shell expressions.
- A fresh independent YR replay produces no unsupported upstream fields or positive-alignment findings.

## Self-Review

No internal spec-document-reviewer subagent is available in this runtime. Self-review checked that the schema remains conditional and compact for repositories without deployment concerns, that the validator only enforces deterministic shapes, and that no proposed check requires contacting a remote or mutating the audited target. User review is required before implementation planning.
