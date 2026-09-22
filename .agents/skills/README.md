# Repo-Scoped `.agents/skills`

This directory is the runtime-facing compatibility layer for repo-owned skills.

Rules:

- Do not author skill content directly here.
- Prefer symlinks from this directory to `../../skills/<skill-name>` for ordinary repo-owned Skills.
- Governance Skills may point to `../../skills-governance/skills/<skill-name>`; this is their canonical package source.
- Keep repo-owned Skill source in its documented canonical directory; do not infer ownership from the adapter path.
- Keep user-global-only skills in `~/.agents/skills/` instead of copying them here.
