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

Every source cited or relied on by a finding must have its own complete Source Inventory record. This includes global skills, previous conversation evidence, memory results, and user statements. Do not cite a rule under the audit skill when it actually came from another skill.

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

Use `unknown` rather than guessing. Keep the profile compact and independent of a literal branch name:

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

The observed profile describes evidence. A proposed profile is a separate section.

Do not prescribe agent-branded branch prefixes. Report existing naming and prefer purpose-based names only when making a proposal and no project convention says otherwise.

`worktree_location` describes an evidenced preferred or authorized location, not merely an empty directory that happens to exist. Put actual current and historical worktree paths in Observed Facts; use `unspecified` when no location policy is evidenced.

`branch_roles` contains normalized semantic roles from the listed vocabulary. `branch_patterns` contains literal branch names or prefixes such as `fix-*`. Put tags and remote-tracking refs in Observed Facts. Do not treat release tags as branch roles or patterns unless a repository rule explicitly uses them as naming examples.

### Conditional Fork Extension

Add this complete fork extension only when an applicable rule, configured upstream remote or other explicitly identified upstream source, or authoritative project document establishes an upstream/fork relationship:

```text
upstream_base_sync: mirror | periodic-sync | manual | unknown
local_patch_flow: none | upstream-contribution | persistent-local | mixed | unknown
```

One `origin` remote, third-party dependencies, generic contribution prose, and branch names that merely resemble upstream workflows are insufficient. Without qualifying evidence, use core `upstream_mode: none` when absence was directly established or `unknown` when the surface was not inspected, and omit the fork extension.

### Conditional Runtime Extension

When evidence shows that branch or worktree identity affects the executable local runtime, add:

```text
runtime_binding: upstream-install | fork-primary | patch-branch | worktree | unknown
```

Fork and runtime extensions are independent. Runtime evidence does not require fork fields, and fork evidence does not require runtime binding.

### Conditional Deployment Extension

When evidence shows deployment behavior, add this complete conditional extension:

```text
deployment_topology: single-source | component-specific | external | unknown
deployment_bindings: [<component>=branch:<literal>, <component>=branch-pattern:<literal>, <component>=tag-pattern:<literal>, ...]
deployment_triggers: [<component>=merge|push|tag|manual|external|unknown, ...]
preview_behavior: none | branch-preview | pull-request-preview | environment-preview | mixed | unknown
```

The binding and trigger lists must use non-empty, unique component keys and matching component sets. These values describe release-source policy, not proof that a live deployment succeeded. Record provider or runtime status separately with its visibility limit. A local provider project link does not prove a live production binding. “Deployable” means `release-ready` unless actual production binding is evidenced.

Keep Task Execution Constraints separate from Explicit Project Constraints. Run-local read-only, no-fetch, no-worktree, and temporary-report instructions cannot weaken `worktree_policy`, branch policy, or repository constraints and do not establish a project-fit conflict. Difference Findings contains only evidenced differences; correct cached-ref discipline and unregistered sibling directories are observations unless an applicable source creates a disagreement. Use `none discovered` when there is no actual difference. An `enforcement_gap` must state `missing_effective_control` and `proportionality`.

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
| `git_object_confusion` | A local branch, remote-tracking ref, tag, commit, or worktree is presented as another Git object. |
| `enforcement_gap` | A material, deterministic rule lacks an effective proportionate control. |
| `naming_coupling` | A repository convention is unnecessarily tied to one agent or runtime. |

Semantic findings are candidates, not automatic verdicts. State confidence as `high`, `medium`, or `low` and explain what would change the conclusion.

Do not use `stale_guidance` merely because evidence is old or absent. Use it only when newer evidence demonstrates drift; otherwise use `unverifiable_claim` or record an unresolved question. Use `enforcement_gap` only when all of these are established: material consequence, deterministic enforceability, absence of another effective control, and proportionality to the repository's risk and maintenance model. The absence of a local hook alone is not enough.

## 6. Placement Actions

Recommend one action per evidenced difference:

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

Use this shape. The Source Inventory must be a Markdown table with one complete record per relied-on source and these exact columns:

```text
source | class | scope | normativity | freshness | visibility
```

The profile must contain every core field and an adjacent confidence value. Conditional extensions are all-or-nothing when used. Before writing a draft, run `report_path=$(mktemp -t branch-governance-audit)` outside the audited target repository and capture its resolved value. Use that exact path for every write, validator invocation, and final reference; never use an unexpanded shell substitution as a patch path or filename. Resolve `scripts/validate_report.py` from the actual loaded skill directory rather than the audited repository, then validate with `python3 "$validator_path" "$report_path"`.

```text
Audit Scope:
Explicit Project Constraints:
Task Execution Constraints:
Visibility Limits:

Source Inventory:
- one exact source per table row

Observed Facts And Rules:
- statement
  evidence: source and location or command result

Observed Profile:
- field: value
  confidence: high | medium | low

Difference Findings:
- type:
- baseline:
- deviation:
  difference:
  evidence:
  confidence:
  recommended action:
  proposed destination:

Unresolved Questions:
Tooling Opportunity: none | watch | evaluate-now
Smallest Safe Next Step:
```

Each Difference Finding requires `type`, `baseline`, `deviation`, `difference`, `evidence`, `confidence`, `recommended action`, and `proposed destination`. `baseline` names the applicable rule, candidate profile, documented expectation, or source claim; `deviation` names the conflicting source, current evidence, missing guidance destination, or visibility gap. They must be non-empty, distinct, and concrete. For `omission`, the deviation may state `missing guidance` plus material evidence; for `unverifiable_claim`, baseline is the claim and deviation is the visibility gap. If `type` is `enforcement_gap`, also include `missing_effective_control` and `proportionality`. When no difference is established, use the exact value `none discovered`.

Positive alignment belongs in `Observed Facts And Rules`, not in Difference Findings. Correct treatment of cached refs is an observation, and an unregistered directory beside registered worktrees is only a filesystem observation unless an applicable source incorrectly presents it as registered/current or a material guidance omission is evidenced. There is no `keep` finding action; when one side of a real conflict remains unchanged, name the action for the other side.

Every material finding must point to evidence. Label inference and recommendations explicitly so they are not mistaken for repository facts.

## 8. Tooling Escalation

Remind the user to evaluate `agentslint`, `agnix`, or another relevant tool when at least one of these becomes true:

- the same mechanically detectable failure recurs across repositories;
- broken links, duplicated adapters, malformed frontmatter, or profile-schema drift need batch validation;
- a stable profile contract is ready for CI enforcement;
- manual inventory work is repeated often enough to justify a deterministic collector.

Use `watch` when the pattern is emerging but the schema or desired behavior is still changing. Use `evaluate-now` only after verifying the candidate tool's current capabilities against the observed need.

Do not recommend a linter to decide semantic project fit, precedence across runtimes, or whether a rule belongs globally versus locally. Those remain evidence-backed governance judgments.
