# Skills Source And Scope Policy

This policy covers every Skill visible to an agent on the local machine, not only self-authored Skills.

Runtime visibility is not ownership. A Skill may be visible because it is local, bundled with a runtime, introduced by a plugin, installed through Skills CLI, or copied and customized locally. Governance must record the source authority separately from the runtime-facing path.

## Source Classes

- `local_custom`: maintained in this repository or another explicitly managed canonical Git source
- `runtime_builtin`: distributed by the agent runtime and updated with that runtime
- `runtime_plugin`: introduced by an enabled plugin and updated with that plugin
- `skills_cli_managed`: third-party Skill whose upstream and update channel are managed by Skills CLI
- `third_party_unmanaged`: third-party Skill with verified upstream provenance but no confirmed Skills CLI update authority
- `third_party_customized`: third-party Skill with a local wrapper, overlay, fork, patch, or replacement
- `unknown`: source or update authority has not been verified

The machine-readable baseline is [../config/source-types.yaml](../config/source-types.yaml).

## Canonical And Runtime Layers

- Ordinary repo-owned Skills use `skills/<skill-name>/` as canonical source.
- Governance Skills use `skills-governance/skills/<skill-name>/` because they belong to the governance package.
- `.agents/skills/`, `.codex/skills/`, `~/.agents/skills/`, and target project directories are runtime-facing or target-facing layers, not authoring locations.
- An adapter can expose a Skill without transferring its ownership to this repository.

## Upgrade And Customization Rules

Before changing a non-local Skill, capture the current version or content baseline when the source permits it. Review the before/after diff for:

- trigger and scope changes
- new scripts, binaries, network access, or secret handling
- changed instructions or safety boundaries
- changed dependencies and adapter paths
- behavior overlap with another visible Skill

Do not edit a third-party or runtime-provided Skill in place and then call it upstream-managed. Record the local delta explicitly and choose one of the supported future models: wrapper, overlay, fork, policy exclusion, or local replacement. The concrete automation for snapshots, diffs, and rollback is tracked in [../backlog.md](../backlog.md).

## Scope And Generalization

- Keep Skills tied to this repository, its namespace, or its research workflow repo-scoped.
- Keep vault- or project-coupled Skills target-scoped.
- Promote a Skill globally only after a separate generalization and conflict review.
- Do not infer global suitability from repeated installation alone.

## Routing

- Need to understand what is installed, who owns it, or what conflicts: `skills-governance-audit`
- Need to choose reuse, install, fork, or build: `skills-lifecycle-manager`
- Need to restore a confirmed third-party Skill to Skills CLI: `skills-cli-reconcile`
- Need to bring a self-authored Skill into canonical Git: `skills-intake-local`
- Need to evaluate global promotion: `skills-promote-global`
