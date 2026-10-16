# Skill Versioning And Layout

This repository treats local Skills as versioned source assets, while also tracking Skills that are supplied by runtimes, plugins, and third-party installers.

## Canonical Source

- Ordinary repo-managed Skills live at `skills/<skill-name>/`.
- Governance-specific Skills live at `skills-governance/skills/<skill-name>/`.
- A Skill directory should contain `SKILL.md` and only the supporting folders it needs, such as `references/`, `scripts/`, `assets/`, or `evals/`.
- Self-authored Skills discovered in other local projects should be collected into the canonical repository when they are intended for long-term maintenance.

## Runtime And Target Adapters

- `.agents/skills/` and `.codex/skills/` are compatibility layers.
- `~/.agents/skills/` is user-global scope, not the default authoring location for repo-managed Skills.
- External project or vault directories are target-scoped adapters.
- Adapters should normally be symlinks or thin wrappers that point to a source whose ownership is recorded.
- Never treat an adapter path as proof that the target owns the Skill.

## Source Authority And Versions

Different source classes have different version authorities:

- `local_custom`: Git history and tags in the canonical repository
- `runtime_builtin`: runtime release and bundled content
- `runtime_plugin`: plugin version and bundled content
- `skills_cli_managed`: upstream repository plus Skills CLI metadata
- `third_party_unmanaged`: upstream repository with an update channel requiring explicit review
- `third_party_customized`: upstream baseline plus a separately reviewable local delta
- `unknown`: no update until provenance is verified

Before upgrading a non-local Skill, retain a baseline reference and inspect the resulting diff when possible. A successful installer command is not evidence that the new behavior is acceptable.

## Per-Skill Metadata

Keep required metadata in `SKILL.md` frontmatter. The live registry is `skills-governance/config/skills-inventory.json`. Each record stores:

- `source_class` and `authority` (`ownership`, `source_authority`, `update_authority`, `canonical_path`)
- `baseline` plus the drift pointer `baseline_ref`
- `adapters` and `status`
- `review_time` and `current_version` (reserved for drift reports; null until a review or explicit version exists)
- visible paths, install shape, local customization, customization mode, compatibility constraints, and conflict status

For a `content_hash` baseline, `baseline_ref` is `sha256:` plus the SHA-256 of that skill's `SKILL.md`. `current_version` copies `metadata.version` when present. Local skills otherwise stay versioned by Git, so `current_version` stays null.

Machine installs keep the same fields. `authority.canonical_path` is null when this repository is not the source. A self-authored skill that is not in this repo yet stays `local_custom` and records its `~` install path in `visible_paths`. `~` or absolute paths record the install and its symlink adapters. Skills CLI / `~/.agents/.skill-lock.json` installs use `skills_cli_managed`. `baseline.kind` is `upstream_ref`, `baseline_ref` is `github:` plus the lock `source`, `baseline.subject` is the lock `skillPath`, and `current_version` is the lock `skillFolderHash` (null when that hash is empty). App-bundled and vendor-bundled skills use `runtime_builtin`. A vendor bundle is one summary record, not one record per bundled skill. Its coordinate is `runtime:<provider>`: `baseline.subject` is the version file, and `current_version` is that file's version. Enabled plugins that ship skills use one `runtime_plugin` summary per plugin (`scope` `plugin-managed`), with `baseline_ref` `plugin:<host>/<id>` and `current_version` from that plugin's manifest. The host keeps two installs of the same plugin id, such as Codex and Claude Nowledge Mem, from sharing one coordinate. A plugin record does not replace a Skills CLI row for a different install path. Hermes install stamps use `baseVersion` plus the first 10 characters of `commit`, not `displayVersion`. Do not add a second inventory file for Obsidian or Logseq.

`customization_mode` records the customization that applies: `none`, `wrapper`, `overlay`, `fork`, `policy_exclusion`, `local_replacement`, or `unknown`. `compatibility_constraints` records limits that install or customization must keep. Each item has `kind` (`upstream_version`, `runtime`, `adapter`, `policy`, or `unknown`) and a non-empty `value`. `local_customization: none` allows only `none` or `policy_exclusion`. A content delta (`local_delta`) uses `wrapper`, `overlay`, `fork`, `local_replacement`, or `unknown`. `policy_exclusion` is not a content edit; put the exclusion in a `policy` constraint. Choosing the mode is a separate workflow. This registry only stores the result.

Use `metadata.version` only when a Skill needs an explicit compatibility marker. Do not duplicate inventory state in every `SKILL.md`. After editing the registry, run `skills-governance/scripts/validate_skills_inventory.py --check-paths`.

`skills-governance/scripts/drift_report.py` prints the source and version drift report from this file. It is read-only: it does not modify skills, the inventory, or lock files. Reconciliation uses `review_time`, `baseline_ref`, and `current_version`. A matching skill name is not provenance.

## Publishing And Installation

- Git is the version authority for local canonical Skills.
- Runtime and plugin managers remain the authority for their supplied Skills.
- `npx skills` remains the authority for third-party CLI-managed installation and update operations.
- The governance package adds inventory, diff review, customization, and conflict controls around those authorities; it does not replace them.
