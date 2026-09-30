# Upgrade Review

The governance package adds a safety layer around the existing source authority. It does not replace npx skills, a runtime updater, or a plugin manager.

## Community implementations

- Vercel skills provides installation, update, project/global scope, and experimental lockfile restoration. It remains the update authority for skills_cli_managed entries.
- GitHub CLI gh skill update --dry-run provides a provider-specific preflight for GitHub-managed Skills.
- SkillSync provides Git-backed skill snapshots, diffs, history, and restore for a Skills directory.
- skillsctl provides a skill-oriented status, diff, and rollback command surface.
- skill-provenance focuses on bundle hashes, version identity, and provenance verification.

The closest existing implementation for the review gate is [SkilLock](https://github.com/skills-lock/skil-lock): it records a capability lockfile, reports shell/network/path/script deltas, and can block a CI run pending a human approval. [skillshare](https://github.com/runkids/skillshare) is the closest implementation for a single source of truth and cross-runtime synchronization. Neither one records this package's source classes and target adapter paths for runtime-bundled and plugin-provided Skills.

The local command keeps source metadata, adapter paths, exact tree snapshots, and rollback in its own manifest. For structured capability analysis, prefer SkilLock when it is available; the local gate is a conservative fallback for environments where it is not installed. Installation and activation remain delegated to the source authority.

## Local command

Capture a baseline before an upgrade:

    python3 skills-governance/scripts/skill_guard.py snapshot \
      --skill-root /path/to/skills \
      --adapter-root /path/to/.agents/skills \
      --source-class skills_cli_managed \
      --source-authority upstream_repository \
      --metadata '{"package":"owner/repo","ref":"main"}' \
      --output /path/to/.skill-governance/snapshots/before-upgrade

Run the provider update, then inspect the diff. The --gate option exits with status 2 when it finds changed instructions, new files, scripts, network behavior, secret access, trigger changes, or adapter path changes.

    python3 skills-governance/scripts/skill_guard.py diff \
      --snapshot /path/to/.skill-governance/snapshots/before-upgrade \
      --format json --gate

Restore the captured trees after an unacceptable change:

    python3 skills-governance/scripts/skill_guard.py rollback \
      --snapshot /path/to/.skill-governance/snapshots/before-upgrade \
      --force

The command is intentionally conservative. A clean diff is evidence that the captured tree did not produce one of the configured signals; it is not proof that the Skill is safe or semantically correct.
