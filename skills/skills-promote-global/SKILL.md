---
name: skills-promote-global
description: Evaluate whether a locally owned or project-scoped skill should be promoted to global scope by checking coupling, naming, conflict risk, and generalization readiness before any install or move is performed.
metadata:
  owner: agents-lab
  scope: repo
---

# Skills Promote Global

## Purpose

Use this skill when a user explicitly asks to promote a locally owned or project-scoped skill to global scope.

This skill is a **generalization gate**, not a blind move/install action.

It helps answer:

- Is the skill actually suitable for global use?
- What project-, vault-, or machine-specific assumptions still remain?
- Does the current name create collision or ambiguity risk?
- Should the result be promoted now, generalized first, renamed, or explicitly kept local?

## Do Not Use This Skill For

- intaking a local skill into the canonical repo for the first time
- reinstalling a third-party skill through `skills CLI`
- resolving deep dependency conflicts after promotion
- silently copying a local skill into a global directory without review

Use:

- `skills-intake-local` for local canonical-source intake
- `skills-cli-reconcile` for third-party Skills CLI takeover

## Expected Inputs

Minimum:

- `skill_name`
- `skill_path`

Helpful optional context:

- `current_scope`
- `target_global_dir`
- reason for promotion
- list of current consuming projects

## Output Contract

Always report:

- `skill`
- `promotion_decision`
- `provenance`
- `current_scope`
- `coupling_findings`
- `naming_risk`
- `conflict_risk`
- `risk_summary`
- `required_changes`
- `recommended_next_step`

Suggested values:

- `promotion_decision`
  - `promote_now`
  - `generalize_first`
  - `rename_then_promote`
  - `keep_local`

## Related Skills

- `skills-intake-local`
  - use when the skill still needs proper local intake and canonical-source cleanup first
- `skills-cli-reconcile`
  - use when the skill turns out to be third-party rather than local/custom

## Workflow

### 1. Confirm The Request Is Truly About Promotion

This skill should be used when the user explicitly wants one of the following:

- a local skill to become globally available
- a project skill to be reused across projects
- a target-scoped skill to move into a user-global directory

If the user only wants local intake or third-party reinstall, stop and use the appropriate workflow instead.

### 2. Inspect Scope And Coupling

Check whether the skill still depends on:

- repo-only paths
- vault-only paths
- machine-specific absolute paths
- one project's page names, namespaces, or process assumptions
- one user's environment variables or secrets layout

Record each coupling point explicitly.

### 3. Evaluate Naming Fitness

Ask whether the current name:

- is too project-specific
- is too generic
- collides with known third-party skills
- will create confusion in another project

If the current name is misleading outside its original context, prefer renaming before promotion.

### 4. Evaluate Conflict Risk

Check for likely conflicts:

- same-name global skill already exists
- same-name third-party marketplace skill already exists
- another local skill already covers the same workflow
- the promoted skill would shadow a project-scoped skill with different behavior

If conflict is likely, do not promote immediately.

### 5. Decide Promotion Path

Use these defaults:

- `promote_now`
  - only when coupling is low, naming is safe, and conflict risk is low
- `generalize_first`
  - when the skill is reusable but still contains local assumptions
- `rename_then_promote`
  - when the main blocker is naming ambiguity or collision
- `keep_local`
  - when the skill remains tightly bound to one repo, vault, or machine

### 6. Define Required Changes

If promotion is not immediate, identify the smallest change set needed:

- remove hard-coded paths
- replace local page names with parameters
- split local-only and global versions
- rename the skill
- isolate secrets or environment dependencies

### 7. Perform Promotion Only When Cleared

If the result is `promote_now`, the actual install/move step should happen only after the above review is complete.

Promotion should not happen just because:

- the skill was useful twice
- the user wants convenience
- there is pressure to avoid maintaining two versions

If state tracking exists, promotion decisions should update:

- `[[OS-LOG/Skills 当前状态清单]]`

Do not write live inventory state back into:

- `[[OS-RES/Skills 管理与治理原则]]`

## Automatic Execution Boundary

It is usually safe to produce a recommendation automatically.

Do not automatically perform the actual promotion when:

- naming risk exists
- coupling is still high
- a same-name skill already exists
- the skill would likely behave differently across projects

Hard stop conditions:

- the skill is actually third-party
- the current name collides with an existing important global skill
- promotion would create unresolved ambiguity across projects

## Output Template

Use this summary format:

```text
Skill: <name>
Promotion Decision: <promotion_decision>
Provenance: <local / mixed / unknown>
Current Scope: <repo_scoped / target_scoped / candidate_global>
Coupling Findings:
- ...
- ...
Naming Risk: <low / medium / high>
Conflict Risk: <low / medium / high>
Risk Summary: <coupling / naming / conflict / mixed>
Required Changes:
- ...
- ...
Recommended Next Step: <one sentence>
```

## Success Criteria

This skill has succeeded when:

- promotion is treated as a governance decision rather than a directory move
- local assumptions are surfaced explicitly
- collision risk is identified before install
- the user gets a clear promote / generalize / rename / keep-local recommendation
