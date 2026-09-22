---
name: skills-lifecycle-manager
description: Find candidate skills, compare build-vs-install options, run security-aware preinstall checks, and decide whether an existing local, third-party, or new skill path is the best fit.
metadata:
  owner: agents-lab
  scope: repo
---

# Skills Lifecycle Manager

## Purpose

Use this skill for the general lifecycle decision that happens before a Skill is built, installed, customized, or updated, regardless of where the Skill came from.

It helps answer:

- is there already a suitable skill for this need
- should the user install, reuse, fork, or build
- does a candidate skill look safe enough to evaluate further
- what authority will control future updates
- is the next step discovery, takeover, local intake, or promotion review

## Do Not Use This Skill For

- repo-internal ownership audits across a whole skill set
- taking a third-party skill back under `skills CLI`
- intaking a self-authored skill into canonical source control
- evaluating whether an already-local skill should become global
- updating repo-specific inventory, backlog, or conventions docs as a default side effect

Use:

- `skills-governance-audit` for read-only governance scanning
- `skills-cli-reconcile` for third-party Skills CLI takeover
- `skills-intake-local` for self-authored canonical-source intake
- `skills-promote-global` for generalization and global-promotion review

## When To Use

Use this skill when:

- the user asks to find skills for a task or domain
- the user asks whether an existing skill should be installed or reused
- the user asks whether to build a new skill or adopt an existing one
- a local workflow may already exist as a reusable skill elsewhere

## Expected Inputs

Minimum:

- `task_or_domain`

Helpful optional context:

- `current_project`
- `known_local_skills`
- `known_global_skills`
- `known_third_party_candidates`
- `known_runtime_candidates`
- `known_plugin_candidates`
- `constraints`
  - such as security, token budget, portability, or required runtimes

## Output Contract

Always report:

- `task_or_domain`
- `candidate_sources`
- `existing_fit_assessment`
- `security_screening_summary`
- `source_classification`
- `update_authority`
- `customization_options`
- `baseline_and_diff_requirement`
- `recommended_path`
- `why_not_the_other_paths`
- `followups`

Suggested values for `recommended_path`:

- `reuse_local`
- `install_third_party`
- `fork_then_adapt`
- `build_new`
- `route_to_governance_workflow`

Source classification should distinguish:

- local custom
- runtime builtin
- runtime plugin
- Skills CLI managed
- third-party customized
- unknown

## Coordination Boundary

Use `skills-lifecycle-manager` as the user-facing general entry for:

- skill discovery
- build-vs-install decisions
- lightweight security-aware preinstall screening
- deciding which specialized governance workflow should run next

Do not use this skill as the main workflow for:

- conflict scanning across a repo or vault
- ownership reconciliation
- local canonical-source intake
- global-promotion review
- repo-specific documentation synchronization after ownership changes

Compared with `skills-governance-audit`:

- use `skills-lifecycle-manager` when the first question is "should we reuse, install, fork, or build"
- use `skills-governance-audit` when the first question is "what is going on in this skill set"

## Workflow

### 1. Clarify The Need

Define:

- what capability is being requested
- whether it is a one-off workflow or repeatable skill candidate
- whether the user needs local ownership, third-party install, or just a recommendation

### 2. Search Existing Options

Check, in this order when available:

- already-owned local skills
- user-global skills
- third-party install candidates
- runtime-bundled and enabled-plugin candidates

Prefer reuse before new build.

### 3. Assess Fit

For each plausible candidate, judge:

- coverage of the requested task
- coupling to another repo, vault, or machine
- whether the skill is narrow enough to be dependable
- whether the user would still need substantial adaptation

### 4. Do Lightweight Security Screening

Before recommending install or fork, note:

- obvious network or secret-handling behavior
- external scripts or binaries
- heavy or suspicious footprint
- any available risk signal from installer tooling

This is a screening step, not a full audit.

### 5. Establish Upgrade Safety

For an existing non-local candidate, require a baseline and before/after diff review when the source allows it. If a diff cannot be produced, make that limitation explicit and lower confidence in an automatic update.

### 6. Choose The Best Path

Use these defaults:

- `reuse_local`
  - when an existing owned skill already fits with minor or no adaptation
- `install_third_party`
  - when a candidate exists and local ownership is not required
- `fork_then_adapt`
  - when a third-party candidate is close but needs stable local changes
- `build_new`
  - when no suitable option exists
- `route_to_governance_workflow`
  - when the real next step is takeover, intake, audit, or promotion review

### 7. Route To Specialized Workflow When Needed

If the decision exposes a governance problem, route explicitly:

- unclear ownership or overlap across a set -> `skills-governance-audit`
- third-party local copy that should return to installer ownership -> `skills-cli-reconcile`
- self-authored local skill that should move into canonical source control -> `skills-intake-local`
- locally owned skill that may deserve global scope -> `skills-promote-global`
- runtime/plugin supplied skill requiring provider/version review -> `skills-governance-audit`

### 8. Keep Repo-Specific State Updates Out Of Scope By Default

This skill may recommend that another workflow or the caller update:

- inventory notes
- backlog notes
- conventions docs

But it should not treat repo-specific documentation synchronization as a built-in required step.

If the main task has become:

- "scan the whole set and report governance state"
- "update owned-skill inventory"
- "record ownership drift or conflict findings"

route to the appropriate governance workflow instead of expanding this skill.

## Output Template

Use this summary format:

```text
Task Or Domain: <name>
Candidate Sources:
- <local/global/third-party candidate>
Source Classification: <local_custom / runtime_builtin / runtime_plugin / skills_cli_managed / third_party_unmanaged / third_party_customized / unknown>
Update Authority: <git / runtime / plugin / skills CLI / explicit review / unknown>
Existing Fit Assessment:
- <candidate> -> <fit summary>
Security Screening Summary:
- <candidate> -> <signal>
Customization Options:
- <none / wrapper / overlay / fork / policy exclusion / local replacement>
Baseline And Diff Requirement: <required / reviewed / unavailable with limitation>
Recommended Path: <reuse_local / install_third_party / fork_then_adapt / build_new / route_to_governance_workflow>
Why Not The Other Paths:
- <path> -> <reason>
Follow-up:
- <...>
```

## Example

- See `examples/reuse-vs-install-skill.md` for a minimal case where the right answer is to compare local reuse, third-party install, and build-new options before invoking a deeper governance workflow.

## Success Criteria

This skill has succeeded when:

- a user-facing skill lifecycle question gets a clear next step
- reuse is preferred over unnecessary rebuild
- security screening happens before install advice
- deeper governance workflows are invoked only when actually needed
