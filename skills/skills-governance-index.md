# Skills Governance Actions

This note is a lightweight index for the core governance workflows.

## Use These Skills

- `skills-lifecycle-manager`
  - for user-facing skill discovery, build-vs-install decisions, and lightweight preinstall screening
- `skills-cli-reconcile`
  - for third-party skills that should be brought back under `npx skills` management
- `skills-intake-local`
  - for locally owned skills that should be adopted into the canonical skills repository
- `skills-promote-global`
  - for deciding whether a local or project-scoped skill should be generalized and promoted to global scope
- `skills-governance-audit`
  - for read-only scanning of ownership drift, conflict signals, provenance gaps, and naming-taxonomy issues before choosing another workflow

## Decision Shortcut

- If the user first needs to decide reuse vs install vs build -> use `skills-lifecycle-manager`
- If the skill is third-party -> use `skills-cli-reconcile`
- If the skill is self-authored and should be maintained locally -> use `skills-intake-local`
- If the user explicitly wants broader reuse/global availability -> use `skills-promote-global`
- If the first question is “what is going on in this skill set?” -> use `skills-governance-audit`

## Related Principles

- `OS-RES/Skills 管理与治理原则`
- `OS-LOG/Skills 当前状态清单`
