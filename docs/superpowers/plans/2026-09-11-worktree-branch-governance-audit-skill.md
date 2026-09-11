# Worktree And Branch Governance Audit Skill Implementation Plan

> **For agentic workers:** Implement task-by-task. Preserve the read-only audit boundary and run the documented regression cases before considering promotion beyond this repository.

**Goal:** Add a repo-owned skill that audits branch and worktree guidance against project evidence and produces a provenance-aware difference report.

**Architecture:** Keep the trigger and mandatory safety boundary in a concise `SKILL.md`. Put the normalized evidence model and report contract in one directly linked reference. Capture behavioral regression scenarios beside the skill and expose the canonical source through a thin `.agents/skills` adapter.

**Tech Stack:** Markdown skill instructions, Git read-only inspection commands, repository symlink adapter, Codex skill validator.

---

### Task 1: Capture behavioral failures before writing the skill

**Files:**
- Create: `skills/worktree-branch-governance-audit/regression-tests.md`

- [x] Record the current global-default versus repository-fit failure.
- [x] Add production/upstream and local-tool pressure cases.
- [x] Define observable pass/fail criteria without requiring file mutation.

### Task 2: Define the audit evidence model

**Files:**
- Create: `skills/worktree-branch-governance-audit/references/audit-model.md`

- [x] Define source classes, visibility boundaries, and trust handling.
- [x] Define the observed profile fields.
- [x] Define difference types and placement actions.
- [x] Define the report contract with evidence and confidence.
- [x] Define when recurring findings should trigger a tooling evaluation reminder.

### Task 3: Write the minimal skill entrypoint

**Files:**
- Create: `skills/worktree-branch-governance-audit/SKILL.md`

- [x] Add a discriminating trigger description.
- [x] Put the read-only and project-fit boundaries in the first hop.
- [x] Route to the audit-model reference.
- [x] Specify discovery, classification, comparison, and reporting steps.

### Task 4: Expose and document the repo-owned skill

**Files:**
- Create: `.agents/skills/worktree-branch-governance-audit` symlink
- Create: `.codex/skills/worktree-branch-governance-audit` symlink
- Modify: `skills/skills-governance-index.md`

- [x] Add thin shared and Codex adapters pointing to the canonical skill.
- [x] Add the new skill's boundary and routing rule to the governance index.

### Task 5: Validate structure and behavior

**Files:**
- Validate: `skills/worktree-branch-governance-audit/`

- [x] Run the Codex skill validator.
- [x] Check links and adapter targets.
- [x] Dry-run the skill against this repository and compare the result with all regression criteria.
- [x] Inspect the final diff and confirm no existing user changes were overwritten.
