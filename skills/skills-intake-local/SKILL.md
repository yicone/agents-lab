---
name: skills-intake-local
description: Intake a locally owned skill into the canonical skills repository by confirming ownership, normalizing structure, choosing the right scope, creating adapter links, and recording the result.
metadata:
  owner: agents-lab
  scope: repo
---

# Skills Intake Local

## Purpose

Use this skill when a skill is locally owned and should be brought under the canonical Git repository for long-term maintenance.

It can be used for either:

- a single-skill intake
- a batch or directory-level normalization event involving multiple local skills with the same trigger

This skill is for **locally owned skills only**.

It helps answer:

- Is this skill truly local/custom rather than third-party?
- Should it be maintained in the canonical skills repo?
- Is it repo-scoped, target-scoped, or globalizable?
- What should become canonical source vs. runtime-facing adapter?
- What metadata, links, or state notes should be updated?

## Do Not Use This Skill For

- reinstalling third-party skills through `skills CLI`
- promoting a skill to global scope without first generalizing it
- resolving complex cross-skill conflicts
- silently copying a third-party skill into the canonical repo

If the skill is third-party, stop and use the CLI-reconcile workflow instead.

## Expected Inputs

Minimum:

- `skill_name`
- `skill_path`

Helpful optional context:

- `target_project`
- `target_skill_dir`
- `case_classification`
- `shared_trigger`
- `batch_members`
- whether the skill should remain target-scoped only
- known notes about current usage

## Output Contract

Always report:

- `case_classification`
- `shared_trigger`
- `skill`
- `ownership_classification`
- `scope_decision`
- `provenance`
- `canonical_source_path`
- `current_install_shape`
- `adapter_strategy`
- `risk_summary`
- `changes_made`
- `followups`

For batch or directory-level cases, also report:

- `batch_members`
- `verification_result`
- `per_skill_records`

Suggested values:

- `case_classification`
  - `single_skill_intake`
  - `runtime_adapter_normalization`
  - `batch_local_intake`
- `ownership_classification`
  - `local_custom`
  - `local_custom_generalizable`
  - `not_local_stop`
- `scope_decision`
  - `repo_scoped`
  - `target_scoped`
  - `candidate_global`

## Related Skills

- `skills-cli-reconcile`
  - use when the skill is actually third-party and should return to Skills CLI ownership
- `skills-promote-global`
  - use when the user explicitly wants to evaluate global promotion

## Workflow

### 1. Confirm Local Ownership

Check whether the skill is truly local/custom:

- the user explicitly says it is self-authored
- the skill is tightly coupled to a specific vault, repo, or personal workflow
- there is no reliable third-party provenance
- the current directory contains local assumptions that would not make sense as an upstream install

If the skill is actually third-party, stop and route to the CLI-reconcile workflow instead.

If the trigger is that a runtime-facing directory was mistakenly acting as source of truth, classify the case as:

- `runtime_adapter_normalization`

### 2. Decide Scope

Classify the skill as one of:

- `repo_scoped`
  - tied to this repository's workflow or namespace
- `target_scoped`
  - maintained here but intentionally adapted only into an external project, vault, or environment
- `candidate_global`
  - potentially reusable across projects, but only after a separate generalization pass

Do not automatically treat reuse in two places as proof that the skill should be global.

### 3. Create Or Normalize Canonical Source

Canonical source belongs under:

- `<skills-canonical-repo>/skills/<skill-name>/`

Normalize the structure:

- keep `SKILL.md`
- keep only the supporting folders the skill really needs
- remove accidental clutter if it is clearly not part of the skill
- preserve meaningful references, scripts, examples, prompts, or assets

### 4. Separate Source From Adapter Layers

Do not keep real source in:

- `.agents/skills/`
- `.codex/skills/`
- `~/.agents/skills/`
- external target project skill directories

Those should be treated as runtime-facing adapter or consumption layers.

If the case affects multiple local skills at once, apply the same source-versus-adapter normalization rule consistently across all affected members.

Choose the adapter strategy:

- repo-scoped symlink under this repo's `.agents/skills/`
- runtime-specific compatibility symlink such as `.codex/skills/`
- target-only symlink into an external project or vault
- no adapter in this repo if the skill is intentionally target-scoped elsewhere

### 5. Record Metadata

When useful, add metadata in `SKILL.md` frontmatter, such as:

- `owner`
- `scope`
- `adapter_targets`
- optional `version`

Do not add metadata just to mirror fields that are already obvious unless it helps future governance.

### 6. Update State Tracking

If a state note or registry exists, record:

- that the skill is locally owned
- its canonical source path
- the adapter target
- whether it is repo-scoped or target-scoped

Default state note:

- `[[OS-LOG/Skills 当前状态清单]]`

Do not write live inventory state back into:

- `[[OS-RES/Skills 管理与治理原则]]`

### 7. Document Follow-up Work

Call out any remaining work, such as:

- rename needed to avoid conflict
- still too coupled to a machine-specific path
- candidate for later global promotion
- missing tests, examples, or references

## Batch And Directory-Level Cases

Use a two-layer output when the event is larger than one skill:

- case-level result
- per-skill records

Typical triggers:

- a runtime-facing directory was being treated as the source of truth
- multiple local skills were renamed or normalized together
- adapter targets were corrected in bulk
- a canonical repo was introduced after skills had been scattered across targets

In those cases:

- classify the case first
- name the shared trigger explicitly
- then list per-skill records using the normal intake fields

## Automatic Execution Boundary

It is usually safe to proceed automatically when:

- the user clearly identifies the skill as self-authored
- the target canonical repo is known
- the adapter destination is clear
- there is no ambiguity about third-party provenance

Stop and ask before continuing when:

- the skill might actually be third-party
- the skill has both local and upstream-looking versions
- global promotion is being requested without generalization
- the adapter target is unclear and changes could affect another environment

Hard stop conditions:

- confirmed third-party provenance exists
- canonical-source destination is unknown
- the intended adapter target could impact another environment and has not been confirmed

## Output Template

Use this summary format:

```text
Case Classification: <single_skill_intake / runtime_adapter_normalization / batch_local_intake>
Shared Trigger: <none / runtime-facing directory acting as source / bulk adapter normalization / ...>
Skill: <name>
Ownership: <ownership_classification>
Provenance: <local / mixed / unknown>
Scope: <scope_decision>
Current Shape: <real directory / symlink / mixed>
Canonical Source: <path>
Adapter Strategy: <repo / target / runtime-specific / none>
Risk Summary: <none / source_of_truth_drift / adapter_misuse / coupling / ambiguity>
Changes Made:
- ...
- ...
Follow-up:
- ...
- ...
```

For batch or directory-level cases, use this expanded format:

```text
Case Classification: <runtime_adapter_normalization / batch_local_intake>
Shared Trigger: <...>
Batch Members:
- <skill-a>
- <skill-b>
Verification Result: <what was normalized and how it was checked>
Per-Skill Records:
- Skill: <name>
  Ownership: <ownership_classification>
  Scope: <scope_decision>
  Canonical Source: <path>
  Adapter Strategy: <...>
  Risk Summary: <...>
  Changes Made: <...>
  Follow-up: <...>
```

## Example

- See `examples/runtime-adapter-normalization.md` for a directory-level local intake case where multiple ACNS skills were normalized from runtime-facing adapters back into the canonical repo.

## Success Criteria

This skill has succeeded when:

- a locally owned skill now has one clear canonical source
- runtime-facing directories are no longer treated as source of truth
- adapter scope is explicit
- the skill is easier to maintain than before
- future agents can discover ownership and structure without re-deriving it
