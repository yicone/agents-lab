# Repo-Scoped Skills

Skills that are tightly bound to this repository should remain repo-scoped.
The workflow itself should stay portable across agent runtimes even when one runtime stores its adapter files in a tool-specific directory.

## Why

If a skill assumes:

- the namespace `proj/agents-lab`
- this repository's paths
- this repository's research workflow

then exposing it globally risks contaminating work in other projects.

## Default Policy

- Keep durable instructions in `docs/` when they should apply across tools.
- Prefer storing runtime-specific repo-scoped skill adapters under that runtime's local convention, such as `.codex/skills/` for Codex.
- For cross-client interoperability, prefer `.agents/skills/` as the shared runtime-facing convention.
- Keep the canonical checked-in source under `skills/`, and mirror or link runtime-facing entries back to it.
- Only make a global skill when the instructions are generalized and parameterized

## Version-Management Policy

- Treat `skills/` as the canonical version-controlled source tree for repo-owned skills.
- Treat `.agents/skills/` as a compatibility layer, not the primary authoring location.
- Do not keep editing repo-owned skills directly in `~/.agents/skills/`; intake them into `skills/` first.
- See [skills-versioning.md](skills-versioning.md) for the detailed layout and versioning rules.

## Requirement for Generalized Skills

A globalizable skill must not hard-code:

- `proj/agents-lab`
- repository-only file paths
- repository-only assumptions about workflow
