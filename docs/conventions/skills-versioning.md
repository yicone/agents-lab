# Skill Versioning And Layout

This repository treats local skills as versioned source assets, not as ad hoc files under a home directory.

## Canonical Source

- `skills/` is the canonical source tree for repo-managed skills.
- Each skill lives at `skills/<skill-name>/`.
- The skill directory should contain `SKILL.md` and only the supporting folders it actually needs, such as `references/`, `scripts/`, `assets/`, or `evals/`.

## Runtime Compatibility Layer

- `.agents/skills/` is the repo-scoped compatibility layer for agents that scan the shared `.agents/skills/` convention.
- Entries under `.agents/skills/` should normally be symlinks pointing back to `../../skills/<skill-name>`.
- Do not treat `.agents/skills/` as the place to author skill content.
- If a runtime still scans a runtime-specific path such as `.codex/skills/`, keep that path as a compatibility layer only and point it back to `../../skills/<skill-name>` as well.

## Target-Scoped Adapters Outside This Repository

- Some repo-managed skills are intentionally adapted only into a specific external project or vault.
- In those cases, keep the canonical source in this repository under `skills/`, but do not expose the skill through this repository's own `.agents/skills/` unless it is meant to be consumed here.
- Instead, point the target project's `.agents/skills/<skill-name>` entry directly at this repository's `skills/<skill-name>`.
- Record the target scope and provenance in the skill frontmatter when that routing is important.

## User Scope

- `~/.agents/skills/` is user scope, not the primary authoring location for repo-managed skills.
- A skill should stay in `~/.agents/skills/` only if it is intentionally global and not owned by this repository.
- If a skill becomes repo-owned, move its canonical source into `skills/` and expose it through `.agents/skills/` instead of editing the home-directory copy in place.

## Versioning Policy

- Default version authority is Git history in this repository.
- Use commits and tags for real version boundaries.
- Add an explicit per-skill version only when at least one of these is true:
  - the skill is shared across repositories
  - the skill needs compatibility promises
  - the skill is distributed outside this repository
  - the skill needs independent rollback tracking

## Per-Skill Metadata

- Keep required metadata in `SKILL.md` frontmatter.
- If a skill needs an explicit version marker, prefer `metadata.version` in `SKILL.md`.
- If a separate manifest is useful for tooling, keep it beside the skill as an additional file, but do not duplicate required frontmatter fields there without a reason.

Example:

```yaml
---
name: example-skill
description: Use when the user asks for an example.
metadata:
  owner: agents-lab
  scope: repo
  version: "0.1.0"
  adapter_targets:
    - /absolute/path/to/project/.agents/skills/example-skill
---
```

## Intake Workflow For Existing Local Skills

When adopting an already-existing local skill:

1. Copy its canonical contents into `skills/<skill-name>/`
2. Normalize structure to the Agent Skills convention
3. Decide whether any private or machine-local material must stay out of Git
4. Add the repo-scoped `.agents/skills/` symlink
5. Update docs if the skill changes the repository's durable conventions

## Publishing And Installation

- Being in Git is the primary version-management mechanism.
- Tools such as `npx skills` are useful for discovery, installation, and update workflows.
- They do not replace the repository's canonical source policy.
