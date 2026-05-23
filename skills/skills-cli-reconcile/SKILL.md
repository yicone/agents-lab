---
name: skills-cli-reconcile
description: Reconcile an existing third-party skill back under Skills CLI management by confirming provenance, classifying the local copy, reinstalling from upstream when safe, and recording risk and follow-up notes.
metadata:
  owner: agents-lab
  scope: repo
---

# Skills CLI Reconcile

## Purpose

Use this skill when an existing skill is already present in a project, vault, or runtime-facing skill directory, but should be brought back under `npx skills` management.

This skill is for **third-party skills only**.

It helps answer:

- Is this skill really third-party rather than locally owned?
- What is its upstream provenance?
- Is the current local copy original, outdated, modified, or already CLI-managed?
- Can it be safely reinstalled through `skills CLI`?
- What risks or follow-up actions should be recorded?

## Do Not Use This Skill For

- adopting a locally authored skill into the canonical repo
- promoting a local or project skill to global scope
- resolving deep multi-skill conflicts
- rewriting or redesigning a skill's contents

If the skill is locally owned, stop and use the local-intake workflow instead.

## Expected Inputs

Minimum:

- `skill_name`
- `skill_path`

Helpful optional context:

- `known_provenance`
- `target_project`
- `state_note_path`
- whether reinstall/overwrite is allowed

## Output Contract

Always report:

- `skill`
- `classification`
- `provenance`
- `scope`
- `current_install_shape`
- `action_taken`
- `risk_summary`
- `required_changes`
- `followups`

Suggested values:

- `classification`
  - `third_party_cli_managed`
  - `third_party_outdated_copy`
  - `third_party_modified`
  - `third_party_unknown_upstream`
- `current_install_shape`
  - `real directory`
  - `symlink`
  - `mixed`
- `scope`
  - `third_party`
  - `third_party_target_scoped`

## Related Skills

- `skills-intake-local`
  - use when the skill is actually locally owned and should move into the canonical repo
- `skills-promote-global`
  - use when the user wants broader/global reuse rather than CLI reconciliation

## Workflow

### 1. Confirm It Is Not Locally Owned

Check whether the skill is already treated as local/custom:

- it already lives under the canonical repo's `skills/`
- the user has explicitly said it is self-authored
- prior state notes or governance docs classify it as local/custom
- the skill is tightly coupled to repo-only or vault-only workflow and is intentionally maintained locally

If it is locally owned, stop and report that this skill should not be reconciled through `skills CLI`.

### 2. Inspect Current Shape

Check:

- whether the current `skill_path` is a real directory or symlink
- whether runtime-specific paths such as `.claude/skills/` or `.codex/skills/` are only exposure layers
- whether the local copy contains scripts, assets, or dependencies that may affect reinstall safety

### 3. Confirm Provenance

Use this order:

1. user-provided provenance
2. existing state note / registry / inventory
3. `npx skills find <skill_name>`
4. known GitHub repository URL
5. content comparison with upstream `SKILL.md`

If provenance still cannot be confirmed, classify as `third_party_unknown_upstream`, do not reinstall, and output a follow-up asking for source confirmation.

### 4. Classify Local State

Determine whether the local copy is:

- already CLI-managed
- an outdated copy
- a modified local copy
- source-confirmed but not yet CLI-managed

Key signals:

- can the skill be reinstalled from a known source?
- does current content match upstream?
- does the current directory look like a CLI/installer result?
- is there evidence of manual local edits?

### 5. Reinstall Through Skills CLI When Safe

If provenance is known and overwrite is allowed:

- prefer `npx skills add owner/repo@skill`
- if `find` does not expose the skill but the repository is known, use:
  - `npx skills add <repo-url> --skill <skill_name>`

After reinstall, verify:

- the actual install directory now reflects Skills CLI ownership
- runtime-specific directories remain only as exposure/symlink layers where expected

### 6. Record Risks

Preserve the installation summary from `skills CLI`, especially:

- Gen risk
- Socket alerts
- Snyk risk

Do not treat successful installation as a substitute for risk review.

### 7. Update State Tracking

If a state note or inventory exists, write back:

- provenance
- classification
- Skills CLI takeover status
- risk notes
- recent change summary

Default state note:

- `[[OS-LOG/Skills 当前状态清单]]`

Do not write live inventory state back into:

- `[[OS-RES/Skills 管理与治理原则]]`

## Automatic Execution Boundary

It is usually safe to proceed automatically when:

- provenance is clear
- the user has allowed takeover
- there is no evidence of meaningful local modification
- overwrite is reversible

Stop and ask before continuing when:

- local manual edits are likely
- multiple upstream candidates remain plausible
- risk is high and the skill's necessity is unclear
- reinstall would overwrite unreviewed user changes

Hard stop conditions:

- evidence suggests the skill is locally owned
- the only available action would destroy meaningful local modifications
- provenance is still ambiguous after normal checks

## Output Template

Use this summary format:

```text
Skill: <name>
Classification: <classification>
Provenance: <owner/repo@skill or unknown>
Scope: <third_party / third_party_target_scoped>
Current Shape: <real directory / symlink / mixed>
Action Taken: <none / CLI reinstalled / marked unknown / skipped>
Risk Summary: <gen/socket/snyk>
Required Changes:
- ...
- ...
Follow-up:
- ...
- ...
```

## Example

- See `examples/minimal-cli-reconcile.md` for a minimal third-party takeover case with known provenance and preserved risk notes.

## Success Criteria

This skill has succeeded when:

- a third-party skill's provenance is clearer than before
- safe candidates are brought back under `skills CLI`
- risk signals are preserved
- locally owned skills are not mistakenly treated as third-party
- the inventory or state note becomes more accurate
