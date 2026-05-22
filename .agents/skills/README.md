# Repo-Scoped `.agents/skills`

This directory is the runtime-facing compatibility layer for repo-owned skills.

Rules:

- Do not author skill content directly here.
- Prefer symlinks from this directory to `../../skills/<skill-name>`.
- Keep repo-owned skill source in `skills/`.
- Keep user-global-only skills in `~/.agents/skills/` instead of copying them here.
