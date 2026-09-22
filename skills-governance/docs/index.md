# Skills Governance Actions

This note is a lightweight index for the core governance workflows. Read `../README.md` for the package scope and `../config/source-types.yaml` for the source-class model.

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
  - for read-only scanning of source authority, ownership drift, conflict signals, provenance gaps, update drift, and naming-taxonomy issues before choosing another workflow
- `worktree-branch-governance-audit`
  - for read-only reconciliation of branch and worktree guidance against project-specific evidence, including global-default fit, upstream topology, deployment coupling, and rule placement

## Decision Shortcut

- If the user first needs to decide reuse vs install vs build -> use `skills-lifecycle-manager`
- If the skill is third-party -> use `skills-cli-reconcile`
- If the skill is self-authored and should be maintained locally -> use `skills-intake-local`
- If the user explicitly wants broader reuse/global availability -> use `skills-promote-global`
- If the first question is “what is going on in this skill set?” -> use `skills-governance-audit`
- If a non-local Skill is about to be upgraded -> first capture a baseline and route through `skills-governance-audit` or `skills-cli-reconcile` before applying the change
- If the question is whether branch or worktree rules fit a repository, conflict across AGENTS.md/skills/docs, or belong at another scope -> use `worktree-branch-governance-audit`

## Related Principles

- `OS-RES/Skills 管理与治理原则`
- `OS-LOG/Skills 当前状态清单`
