# Repo-Scoped Skills

Skills that are tightly bound to this repository should remain repo-scoped.

## Why

If a skill assumes:

- the namespace `proj/agents-lab`
- this repository's paths
- this repository's research workflow

then exposing it globally risks contaminating work in other projects.

## Default Policy

- Prefer storing repo-specific skills under `.codex/skills/`
- If you need a checked-in source path, mirror or link it under `skills/`
- Only make a global skill when the instructions are generalized and parameterized

## Requirement for Generalized Skills

A globalizable skill must not hard-code:

- `proj/agents-lab`
- repository-only file paths
- repository-only assumptions about workflow
