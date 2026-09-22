---
name: skills-governance-audit
description: Use when scanning a project, vault, or skill directory for ownership drift, provenance gaps, conflict signals, naming-taxonomy inconsistencies, or unclear routing between existing skills.
metadata:
  owner: agents-lab
  scope: repo
---

# Skills Governance Audit

## Purpose

Use this skill when the goal is to audit a directory, runtime, plugin surface, or Skill set before deciding which governance action should run next.

This skill is a **read-only governance scanner**.

It helps answer:

- what skills exist in the scanned scope
- which ones are local, runtime-bundled, plugin-provided, Skills CLI-managed, customized, or unclear
- whether there are conflict signals between skills
- whether naming conventions look coherent
- which follow-up workflow should handle each finding

## Do Not Use This Skill For

- intaking a local skill into the canonical repo
- reinstalling a third-party skill through `skills CLI`
- promoting a skill to global scope
- silently renaming or moving skills during the audit itself

Use:

- `skills-intake-local` for local canonical-source intake
- `skills-cli-reconcile` for third-party takeover
- `skills-promote-global` for global-promotion review
- `skills-lifecycle-manager` for reuse/install/fork/build decisions across source classes

## Expected Inputs

Minimum:

- `audit_scope`
  - one directory, one project, or one named skill set

Helpful optional context:

- `known_targets`
- `known_local_skills`
- `known_third_party_skills`
- `known_runtime_skills`
- `known_plugin_skills`
- `known_cli_sources`
- `focus`
  - `ownership`
  - `provenance`
  - `conflicts`
  - `naming`
  - `full`

## Output Contract

Always report:

- `audit_scope`
- `scope_summary`
- `source_classifications`
- `local_skills`
- `third_party_skills`
- `unknown_skills`
- `source_authority_findings`
- `upgrade_drift_findings`
- `conflict_findings`
- `naming_findings`
- `routing_recommendations`
- `state_update_suggestions`
- `followups`

Suggested conflict types:

- `name_collision`
- `scope_collision`
- `behavior_overlap`
- `dependency_conflict`
- `path_conflict`
- `responsibility_overlap`

Suggested naming findings:

- `consistent`
- `taxonomy_mixed_but_explained`
- `naming_taxonomy_drift`
- `rename_candidate`

## Related Skills

- `skills-intake-local`
  - use when the audit finds local skills that should move under canonical source control
- `skills-cli-reconcile`
  - use when the audit finds third-party skills that should return to Skills CLI ownership
- `skills-promote-global`
  - use when the audit finds a local skill that may need global-promotion review

## Workflow

### 1. Define Audit Scope

Decide whether the audit is about:

- one skill
- one runtime-facing directory
- one repository's canonical skills set
- one target project or vault

Keep the scope explicit in the output.

### 2. Inventory What Exists

Scan the requested scope and list:

- canonical source entries under `skills/`
- governance source entries under `skills-governance/skills/`
- runtime-facing adapters under `.agents/skills/` or `.codex/skills/`
- runtime-bundled and plugin-provided entries when the runtime exposes them
- Skills CLI install records and lock metadata when available
- externally adapted targets when they are part of the case

Do not assume every visible entry is a true source directory.

### 3. Classify Source Authority At A High Level

For each relevant skill, classify whether it looks:

- `local_custom`
- `runtime_builtin`
- `runtime_plugin`
- `skills_cli_managed`
- `third_party_unmanaged`
- `third_party_customized`
- `unknown`

Runtime visibility is not proof of ownership. Record the source authority and update channel separately from the visible path.

### 4. Check Upgrade Drift And Customization

When a baseline or upstream version is available, compare it with the current copy. Record:

- version or commit change
- changed trigger and scope text
- new scripts, binaries, network calls, or secret handling
- local edits that would be lost by replacement

If no baseline can be captured, report `unverified` rather than implying that the Skill is current.

### 5. Detect Conflict Signals

Look for conflicts such as:

- two skills competing for the same name
- one global skill shadowing a narrower-scoped local skill
- two skills appearing to solve the same user need with different rules
- one runtime-facing path acting as source while another canonical source also exists
- two closely related skills whose trigger boundaries are too ambiguous

When trigger ambiguity is the main issue, classify it as:

- `responsibility_overlap`

Use this especially when an agent may reasonably hesitate between two skills because each appears eligible for the same request.

### 6. Check Naming Taxonomy

Audit whether current names follow a coherent taxonomy:

- prefix families match actual responsibility boundaries
- policy-layer skills are distinguishable from transport-layer skills
- user-facing workflow skills are distinguishable from governance or infrastructure skills

Do not demand one prefix for everything.

Mixed prefixes are acceptable when they reflect a real taxonomy.

Flag a naming issue only when:

- similar skills use different names for no clear semantic reason
- one family mixes policy and transport without distinction
- a rename would materially reduce routing confusion

### 7. Route Findings To The Right Workflow

For each finding, recommend the next step:

- `skills-intake-local`
- `skills-cli-reconcile`
- `skills-promote-global`
- `manual clarification only`
- `runtime/plugin review`

Do not perform the follow-up action inside the audit.

### 8. Suggest State Updates

If a state note or backlog should change, suggest:

- inventory updates
- backlog entries
- conflict notes
- naming-taxonomy follow-ups

Default state destinations:

- `[[OS-LOG/Skills 当前状态清单]]`
- `[[OS-LOG/Skills 治理待办与未决事项]]`

Do not write live inventory or backlog state into:

- `[[OS-RES/Skills 管理与治理原则]]`

## Automatic Execution Boundary

It is usually safe to run the audit automatically because it is read-only.

Stop and ask before continuing when:

- the user expects the audit to mutate files or directories immediately
- the audit depends on uncertain external targets that are not accessible
- a naming change would break active adapters and the user has not asked for renames yet

Hard stop conditions:

- the only way to answer is to mutate active skill layouts
- audit scope is too vague to distinguish source from adapter layers

## Output Template

Use this summary format:

```text
Audit Scope: <path / project / skill set>
Scope Summary:
- ...
- ...
Source Classifications:
- <skill> -> <local_custom / runtime_builtin / runtime_plugin / skills_cli_managed / third_party_unmanaged / third_party_customized / unknown>
Source Authority Findings:
- <skill> -> <provider / repository / plugin / upstream / unknown>
Upgrade Drift Findings:
- <skill> -> <baseline / changed scope / changed behavior / unavailable>
Local Skills:
- <skill>
Third-Party Skills:
- <skill>
Unknown Skills:
- <skill>
Conflict Findings:
- Type: <responsibility_overlap / ...>
  Skills: <skill-a>, <skill-b>
  Why It Matters: <...>
  Recommended Winner Or Boundary: <...>
Naming Findings:
- Status: <consistent / taxonomy_mixed_but_explained / naming_taxonomy_drift / rename_candidate>
  Evidence: <...>
Routing Recommendations:
- <skill or finding> -> <skills-intake-local / skills-cli-reconcile / skills-promote-global / runtime/plugin review / manual clarification only>
State Update Suggestions:
- <...>
Follow-up:
- <...>
```

## Example

- See `examples/repo-scan-with-conflicts.md` for a repository-scope audit that surfaces responsibility overlap and naming-taxonomy questions without mutating any skill.

## Success Criteria

This skill has succeeded when:

- the current skill set is easier to reason about than before
- ownership, conflict, and naming problems are separated clearly
- follow-up work is routed to the right governance workflow
- no accidental mutation happens during the audit
