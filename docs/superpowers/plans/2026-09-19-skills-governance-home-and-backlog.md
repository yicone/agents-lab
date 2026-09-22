# Skills Governance Home And Backlog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create one maintained home for local-machine Skills governance and extend its model to cover runtime-provided, plugin-provided, Skills CLI-managed, locally customized, and self-authored Skills.

**Architecture:** Keep ordinary canonical Skills under `skills/<skill-name>/`. Create a separate `skills-governance/` package for governance-specific Skills, documents, source-class configuration, and backlog. Runtime-facing adapters remain thin links and are updated only after the canonical move is verified.

**Tech Stack:** Markdown, YAML configuration, Git directory moves, symlink verification, repository text checks.

---

### Task 1: Define the dedicated governance package

**Files:**
- Create: `skills-governance/README.md`
- Create: `skills-governance/backlog.md`
- Create: `skills-governance/config/source-types.yaml`
- Create: `skills-governance/docs/`
- Create: `skills-governance/skills/`

- [x] Create the package README with scope, ownership, and adapter rules.
- [x] Define source classes and update authorities in `config/source-types.yaml`.
- [x] Add prioritized backlog items for pre-upgrade diff review, customization, conflict detection, inventory, and runtime/plugin reconciliation.

### Task 2: Move governance assets without changing ordinary skill ownership

**Files:**
- Move: `skills/skills-governance-audit/` -> `skills-governance/skills/skills-governance-audit/`
- Move: `skills/skills-cli-reconcile/` -> `skills-governance/skills/skills-cli-reconcile/`
- Move: `skills/skills-intake-local/` -> `skills-governance/skills/skills-intake-local/`
- Move: `skills/skills-promote-global/` -> `skills-governance/skills/skills-promote-global/`
- Move: `skills/skills-lifecycle-manager/` -> `skills-governance/skills/skills-lifecycle-manager/`
- Move: governance convention docs into `skills-governance/docs/`
- Move: `skills/skills-governance-index.md` -> `skills-governance/docs/index.md`

- [x] Move only governance-specific assets; leave unrelated research and ordinary Skills in place.
- [x] Preserve each Skill's internal references and examples while changing paths that point to moved docs.
- [x] Update repo documentation indexes and session-start references.

### Task 3: Extend the governance model

**Files:**
- Modify: `skills-governance/skills/skills-governance-audit/SKILL.md`
- Modify: `skills-governance/skills/skills-cli-reconcile/SKILL.md`
- Modify: `skills-governance/skills/skills-lifecycle-manager/SKILL.md`
- Modify: `skills-governance/docs/skills.md`
- Modify: `skills-governance/docs/skills-versioning.md`

- [x] Distinguish source authority from runtime visibility and local customization.
- [x] Require baseline snapshot and before/after diff review for upgrades where a diff can be captured.
- [x] Route customized non-local Skills to explicit review rather than silently overwriting them.
- [x] Keep `skills-intake-local` and `skills-promote-global` limited to locally owned Skills.

### Task 4: Reconcile adapters and verify the migration

**Files:**
- Modify: runtime adapter links only when their existing targets become stale.

- [x] Verify every moved Skill resolves from the repository package.
- [x] Update global adapters to the new canonical targets with explicit approval for writes outside the repository.
- [x] Run repository reference scans and a read-only governance audit.
- [x] Confirm unrelated worktree changes remain untouched.

### Task 5: Record delivery state

- [x] Report completed package migration separately from backlog items that remain unimplemented.
- [x] Do not claim runtime/plugin inventory is complete until the actual installed surfaces are scanned.
