# Worktree Branch Governance Schema And Validator Design

## Status

Approved in conversation on 2026-09-12. This design extends the experimental `worktree-branch-governance-audit` skill after real audits of 9router, CodexBar, and AICenter.

## Problem

The first two local-runtime forks exposed remote-evidence and source-attribution failures. The AICenter audit then showed that the short profile assumes the primary branch is literally named `main`, mixes semantic branch roles with literal naming patterns, assigns high confidence to uninspected `unknown` values, and lacks a precise category for confusing local branches, remote-tracking refs, tags, and worktrees.

The report contract also continues to drift: AICenter grouped several plan files under a wildcard Source Inventory entry even though a finding relied on one specific plan. This is the first independent post-revision recurrence and meets the threshold for a deterministic validator.

## Considered Approaches

### Keep the schema and add more prose

Smallest edit, but three audits have already interpreted `branch_families` inconsistently. More explanation has not produced reliable output.

### Replace the core profile with a large universal schema

Would capture every observed repository, but would make simple local tools answer irrelevant deployment and fork questions. This conflicts with the goal of a short profile.

### Revise the small core and add conditional extensions

Chosen. Keep universal identity, branch-role, and worktree-policy fields compact. Enable fork, runtime, and deployment fields only when evidence shows those concerns exist. Pair the Markdown contract with a small validator that checks structure without deciding semantic project fit.

## Profile Schema

The core profile becomes name-independent:

```text
primary_branch: <literal branch name> | unknown
primary_branch_role: production | release-ready | integration | local-default | upstream-mirror | unknown
direct_primary_changes: prohibited | conditional | allowed | unknown
worktree_policy: required | conditional | optional | not-used | unknown
worktree_adoption: active | historical | none | unknown
worktree_location: repository-local | sibling | external | unspecified
branch_roles: [feature, fix, hotfix, upstream-review, upstream-contribution, upstream-adopt, release, experiment]
branch_patterns: [<literal branch or prefix patterns>]
upstream_mode: none | mirror | periodic-sync | selective-adoption | unknown
release_freeze: none | on-demand | persistent | unknown
environment_coupling: none | local-runtime | docker | test-server | preview | production | mixed | unknown
```

`branch_roles` is semantic; `branch_patterns` is literal. Tags and remote-tracking refs are observed Git objects, not branch patterns unless a repository rule explicitly uses them as naming examples.

An `unknown` caused by missing or uninspected evidence has low confidence. A high-confidence negative uses an explicit value such as `none` or `not-used`, with evidence.

## Conditional Extensions

Fork/runtime extension:

```text
upstream_base_sync: mirror | periodic-sync | manual | unknown
local_patch_flow: none | upstream-contribution | persistent-local | mixed | unknown
runtime_binding: upstream-install | fork-primary | patch-branch | worktree | unknown
```

Deployment extension:

```text
production_branch: <literal branch name> | unknown
production_trigger: merge | push | tag | manual | external | unknown
preview_behavior: none | branch-preview | pull-request-preview | mixed | unknown
deployment_binding_confidence: high | medium | low
```

Extensions appear only when relevant evidence exists. A local Vercel project link alone cannot prove the production branch.

## Difference Classification

Add `git_object_confusion` for conflating a local branch, remote-tracking ref, tag, commit, or worktree. Reserve `unverifiable_claim` for material claims lacking accessible evidence. Conflicting or ambiguous normative wording remains `direct_conflict` or `clarify`, depending on whether the prescriptions are actually incompatible.

An `enforcement_gap` requires all of:

- material consequence;
- deterministic enforceability;
- evidence that an effective control is absent;
- a control proportionate to the repository's risk and maintenance model.

The absence of a local hook alone is insufficient. Recommendations must account for a solo-maintainer repository and controls outside the inspected surface.

## Report Validator

Create a dependency-free Python CLI beside the skill. It accepts one Markdown report path and returns nonzero on structural violations.

Initial checks:

- all required headings exist exactly once;
- Source Inventory is a Markdown table with the six required columns;
- Source Inventory source cells do not use wildcard aggregation;
- the core Observed Profile uses only defined keys;
- every core field is present and has an adjacent confidence value;
- `unknown` cannot have high confidence;
- each Difference Finding contains type, evidence, confidence, recommended action, and proposed destination;
- values for enumerated core fields belong to the schema.

The validator does not decide whether evidence is true, a recommendation is wise, a source is applicable, or a global rule fits the project. It validates shape, not governance judgment.

## Files And Boundaries

- Modify `skills/worktree-branch-governance-audit/SKILL.md` for the first-hop schema boundary and validator use.
- Modify `skills/worktree-branch-governance-audit/references/audit-model.md` for the full schema and classification rules.
- Modify `skills/worktree-branch-governance-audit/regression-tests.md` with the AICenter baseline and expected behavior.
- Modify `skills/worktree-branch-governance-audit/references/tooling-evaluation.md` to mark the custom validator `evaluate-now`.
- Create `skills/worktree-branch-governance-audit/scripts/validate_report.py`.
- Create fixtures and unit tests under `skills/worktree-branch-governance-audit/tests/`.

The audit remains read-only with respect to the target repository. Validation may use a report file supplied by the caller or a temporary draft outside the target repository.

## Test Strategy

Start with failing fixtures derived from observed behavior:

- legacy `main_role` multi-branch value;
- literal prefixes placed in semantic roles;
- wildcard Source Inventory source;
- `unknown` with high confidence;
- missing finding destination.

Add one valid minimal report exercising the core schema and one valid deployment extension. Run unit tests with the standard-library `unittest` runner and invoke the CLI against both passing and failing fixtures.

The next commercial-monorepo audit remains the independent behavioral GREEN test. Passing the local parser tests does not prove that an agent will follow the revised skill.

## Self-Review

No spec-reviewer subagent is available in the current runtime. Self-review found the design bounded to one skill, dependency-free, compatible with the repository's no-worktree decision, and explicit about the validator's semantic limits. The user must review this written spec before implementation planning begins.
