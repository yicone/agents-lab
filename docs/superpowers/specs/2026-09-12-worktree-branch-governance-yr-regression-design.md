# Worktree Branch Governance YR Regression Design

## Status

Approved in conversation on 2026-09-12. This design is a corrective increment to `2026-09-12-worktree-branch-governance-schema-validator-design.md`, based on the first independent commercial-monorepo replay against YR.

## Problem

The YR replay proved that the latest skill, temporary-report workflow, and validator can execute successfully. It also exposed schema and instruction defects that allow a structurally valid report to contain invented differences and unsupported deployment semantics.

The report treated the task-local instruction not to create or use a worktree during a read-only audit as a conflict with YR's repository-scoped rule for development work. It then emitted positive observations as `Difference Findings`, coupled unrelated upstream fields to a runtime-only repository, inferred production semantics without deployment evidence, and placed a confidence value next to another confidence field. The validator accepted all of these.

## Considered Approaches

### Add prose only

This would be small, but it would leave the validator contract responsible for several failure-inducing shapes: mandatory findings, a combined fork/runtime group, and nested confidence.

### Correct the schema, validator, and regression suite together

Chosen. Preserve Markdown as the human-readable report format, remove schema pressure that causes fabricated content, and add deterministic checks only where the failure is mechanically recognizable.

### Replace Markdown with a fully structured format

YAML or JSON would permit stronger validation, but would make the audit less readable and is unnecessary for the current experimental stage.

## Constraint Layers

Reports must distinguish three layers:

- `Explicit Project Constraints`: current user decisions that govern the target repository and repository-specific normative boundaries;
- `Task Execution Constraints`: instructions that govern only this audit run, such as read-only operation, no remote contact, or no worktree creation;
- `Audit Scope` and `Visibility Limits`: target boundaries and evidence that was not inspected.

A task execution constraint is not evidence that the repository policy is overbroad. It may produce a project-fit finding only when the user explicitly states that the constraint is intended to govern future repository work, not merely the audit.

`Task Execution Constraints` becomes a required report section. The validator checks presence and uniqueness; semantic placement remains primarily a skill and regression-test responsibility.

## Difference Findings

`Difference Findings` may contain the exact value `none discovered`. The validator must no longer force at least one finding.

When findings exist, each one requires the existing fields plus:

```text
difference: <the incompatible, missing, drifting, or unverifiable relationship>
```

Positive alignment, correct evidence discipline, and well-supported existing policy belong in `Observed Facts And Rules`, not in `Difference Findings`. A cached ref correctly described as cached is not `git_object_confusion`; that type requires an actual source or claim that presents one Git object as another.

An `enforcement_gap` additionally requires:

```text
missing_effective_control: <what material deterministic behavior is not effectively controlled>
proportionality: <why the proposed control fits this repository's risk and maintenance model>
```

These fields make the existing semantic threshold visible and mechanically complete without asking the validator to judge whether the argument is true.

## Conditional Extensions

Replace the combined fork/runtime group with independent extensions.

Fork extension, complete when a fork or upstream relationship is relevant:

```text
upstream_base_sync: mirror | periodic-sync | manual | unknown
local_patch_flow: none | upstream-contribution | persistent-local | mixed | unknown
```

Runtime extension, present when branch or worktree identity affects the executable local runtime:

```text
runtime_binding: upstream-install | fork-primary | patch-branch | worktree | unknown
```

Deployment extension, complete when deployment behavior is relevant:

```text
production_branch: <literal branch name> | unknown
production_trigger: merge | push | tag | manual | external | unknown
preview_behavior: none | branch-preview | pull-request-preview | mixed | unknown
```

Remove `deployment_binding_confidence`. Each deployment field already has its own adjacent `confidence`, so a second confidence field creates meaningless meta-confidence.

The extensions remain conditional. Runtime evidence alone must not force upstream fields, and a fork alone must not force runtime binding.

## Deployment Semantics

`primary_branch_role: production` requires evidence that the branch is bound to production or is the actual production source. A rule that the branch must remain deployable establishes `release-ready`, not production deployment.

When deployment files, provider settings, CI behavior, or authoritative deployment documentation were not inspected, deployment values must remain `unknown` with low or medium confidence. The report must not fill plausible values such as `merge` or `mixed` merely to complete an extension.

## Source Inventory Precision

Every row still represents one source. Extend deterministic validation to reject a source cell containing multiple path-like backtick spans joined into one record. The check should be narrow: it must catch forms such as `` `path/a` and `path/b` `` without rejecting one exact path whose human label contains ordinary prose.

The validator remains unable to determine whether an uncited source was silently used. That remains a behavioral regression concern.

## YR Regression Case

Add YR as a new RED baseline with these observed failures:

- task-local no-worktree execution was treated as a project governance conflict;
- a repository-specific rule was incorrectly targeted with `narrow_global_default`;
- correct cached-ref discipline was emitted as `git_object_confusion`;
- positive worktree-location alignment was emitted as `omission`;
- runtime evidence forced irrelevant upstream fields;
- deployable-state language was promoted to production binding;
- speculative deployment values were emitted despite explicit visibility limits;
- `deployment_binding_confidence` received its own adjacent confidence;
- a compound Git source was represented as one Source Inventory row;
- `enforcement_gap` did not explicitly establish the missing effective control and proportionality.

The corrected behavior must keep task execution constraints separate, permit no differences, use a runtime-only extension, classify `main` as `release-ready` unless stronger deployment evidence is inspected, and leave unknown deployment facts unknown.

## Validator And Fixture Changes

Add or revise fixtures for:

- a valid report with `none discovered` findings;
- a valid runtime-only report;
- a valid fork-only report;
- a valid deployment report without nested confidence;
- a missing `Task Execution Constraints` section;
- an incomplete fork extension;
- the removed `deployment_binding_confidence` field;
- an enforcement gap missing its conditional fields;
- a compound Source Inventory cell.

Existing valid fixtures must adopt the new required section and `difference` field. Existing invalid fixtures should fail for their intended diagnostic rather than an unrelated schema migration error.

## Tooling Boundary

The local dependency-free validator remains the correct evaluate-now tool because the new deterministic failures concern this report contract. `agentslint` and `agnix` remain candidates for instruction and skill-file integrity, not generated-report semantics. Do not introduce either tool in this increment.

## Success Criteria

- The YR failure is recorded as an explicit RED baseline.
- The validator accepts core-only, runtime-only, fork-only, deployment, and no-difference reports.
- The validator rejects incomplete conditional groups, nested deployment confidence, incomplete enforcement-gap reasoning, missing required sections, and compound source rows.
- Existing regression fixtures retain their intended diagnostics.
- The canonical skill and global symlink remain identical.
- A fresh independent YR replay no longer invents a project-fit conflict or positive-alignment findings.

## Self-Review

No internal spec-document-reviewer subagent is available in this runtime. Self-review checked that the design changes only the experimental audit skill and its tests, preserves read-only target-repository behavior, and keeps semantic truth outside the validator. User review is required before implementation planning.
