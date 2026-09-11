# Branch And Worktree Audit Model

Use this reference when performing a full audit or comparing extracted guidance with a proposed short profile.

## Contents

- [1. Source Inventory](#1-source-inventory)
- [2. Bounded Discovery](#2-bounded-discovery)
- [3. Separate Evidence Kinds](#3-separate-evidence-kinds)
- [4. Short Observed Profile](#4-short-observed-profile)
- [5. Difference Types](#5-difference-types)
- [6. Placement Actions](#6-placement-actions)
- [7. Report Contract](#7-report-contract)
- [8. Tooling Escalation](#8-tooling-escalation)

## 1. Source Inventory

For every relevant source, record:

| Field | Meaning |
|---|---|
| `source` | Absolute or repository-relative path, current user statement, or Git fact command |
| `class` | instruction, skill, project doc, Git fact, deployment fact, CI fact, or inference |
| `scope` | user, repository, subtree, task, runtime adapter, or external |
| `normativity` | required, recommended, example, current state, historical, or unknown |
| `visibility` | read, inaccessible, not discovered, or runtime-dependent |
| `freshness` | current, dated, suspected stale, or unknown; for remotes, state whether freshness was remotely verified |

Do not infer one universal precedence model across agent runtimes. Record known runtime semantics and unresolved precedence explicitly. Subject to system safety and permissions, an explicit current user decision about the target repository is stronger project-fit evidence than a generalized global default.

## 2. Bounded Discovery

Start with directly relevant sources:

- user and repository agent instruction files;
- nested instruction files applicable to the audited path;
- repo-owned and visible user-level skills matching branch, worktree, upstream, release, deploy, PR, or cleanup concerns;
- documents directly linked from those instructions or skills;
- `CONTRIBUTING`, workflow, release, deployment, and convention docs;
- read-only Git state and configuration;
- CI and deployment configuration only when it changes branch semantics.

Use `rg` and explicit paths. Do not recursively ingest every prose file by default. List skipped or inaccessible source classes in the report.

## 3. Separate Evidence Kinds

Classify each extracted statement as one of:

- **Rule:** a normative requirement or recommendation.
- **Fact:** a verifiable property, such as `main` triggering production.
- **State:** a temporary branch, worktree, rollout, or migration status.
- **Example:** illustrative but not normative.
- **Inference:** an analyst conclusion derived from other evidence.

Never promote state or examples into durable rules without explicit evidence.

A local remote-tracking ref is cached local state. It may establish what was last fetched, but it cannot establish the remote's current head or a pull request's lifecycle. Label those claims `unknown` or `unverified` unless an authoritative remote source was checked.

## 4. Short Observed Profile

Use `unknown` rather than guessing. Keep the profile compact:

```text
main_role: production | release-ready | integration | local-default | upstream-mirror | unknown
direct_main_changes: prohibited | conditional | allowed | unknown
worktree_mode: required | conditional | optional | not-used | unknown
worktree_location: repository-local | sibling | external | unspecified
branch_families: [feature, fix, hotfix, upstream-review, upstream-adopt, release, experiment]
upstream_mode: none | mirror | periodic-sync | selective-adoption | unknown
release_freeze: none | on-demand | persistent | unknown
environment_coupling: none | local-runtime | docker | test-server | preview | production | mixed | unknown
```

The observed profile describes evidence. A proposed profile is a separate section.

Do not prescribe agent-branded branch prefixes. Report existing naming and prefer purpose-based names only when making a proposal and no project convention says otherwise.

## 5. Difference Types

| Type | Meaning |
|---|---|
| `direct_conflict` | Two applicable sources prescribe incompatible behavior. |
| `project_fit_conflict` | A generalized rule is valid elsewhere but unsupported or harmful for this repository. |
| `omission` | A material project fact has no corresponding guidance. |
| `duplication` | The same durable rule is independently maintained in multiple places. |
| `scope_misplacement` | A rule lives at user/global, repository, subtree, or skill scope that does not match its applicability. |
| `stale_guidance` | A rule describes branches, remotes, environments, or workflows no longer evidenced. |
| `state_policy_confusion` | Temporary state is presented as durable policy, or the reverse. |
| `unverifiable_claim` | A material claim has no accessible evidence. |
| `enforcement_gap` | A deterministic high-risk rule exists only in prose when a mechanical guard is warranted. |
| `naming_coupling` | A repository convention is unnecessarily tied to one agent or runtime. |

Semantic findings are candidates, not automatic verdicts. State confidence as `high`, `medium`, or `low` and explain what would change the conclusion.

Do not use `stale_guidance` merely because evidence is old or absent. Use it only when newer evidence demonstrates drift; otherwise use `unverifiable_claim` or record an unresolved question.

## 6. Placement Actions

Recommend one action per finding:

- `keep`: correct scope, owner, and level of detail;
- `rewrite`: same location, clearer or narrower wording;
- `move_to_repo_entry`: mandatory first-hop project boundary;
- `move_to_project_doc`: durable explanation, facts, or rationale;
- `move_to_project_skill`: low-frequency multi-step repository workflow;
- `narrow_global_default`: generalized skill or user rule overreaches project contexts;
- `adapter_only`: runtime-specific discovery or syntax;
- `enforce_mechanically`: branch protection, CI, linter, schema, or script;
- `retire`: duplicate, obsolete, or superseded guidance;
- `clarify`: evidence is insufficient or choices are materially different.

## 7. Report Contract

Use this shape:

```text
Audit Scope:
Explicit Project Constraints:
Visibility Limits:

Source Inventory:
- source, class, scope, normativity, freshness

Observed Facts And Rules:
- statement
  evidence: source and location or command result

Observed Profile:
- field: value
  confidence: high | medium | low

Difference Findings:
- type:
  current sources:
  evidence:
  why it matters:
  confidence:
  recommended action:
  proposed destination:

Proposed Profile:
- include only when evidence supports a recommendation

Unresolved Questions:
Tooling Opportunity: none | watch | evaluate-now
Tooling Reason:
Smallest Safe Next Step:
```

Every material finding must point to evidence. Label inference and recommendations explicitly so they are not mistaken for repository facts.

## 8. Tooling Escalation

Remind the user to evaluate `agentslint`, `agnix`, or another relevant tool when at least one of these becomes true:

- the same mechanically detectable failure recurs across repositories;
- broken links, duplicated adapters, malformed frontmatter, or profile-schema drift need batch validation;
- a stable profile contract is ready for CI enforcement;
- manual inventory work is repeated often enough to justify a deterministic collector.

Use `watch` when the pattern is emerging but the schema or desired behavior is still changing. Use `evaluate-now` only after verifying the candidate tool's current capabilities against the observed need.

Do not recommend a linter to decide semantic project fit, precedence across runtimes, or whether a rule belongs globally versus locally. Those remain evidence-backed governance judgments.
